"""端到端流水线（纯函数式，便于单测与复用）。

    转写文本 -> [去标识化] -> [术语纠正] -> SOAP 抽取 -> [LLM 精修] -> 模板填充 -> 校验
"""

from __future__ import annotations

from .config import Settings
from .deidentify import deidentify_segments
from .extractor import ExtractionRules, extract_elements, unclassified_count
from .generator import generate_report
from .llm import LLMRefiner
from .schemas import AuditEvent, GenerateRequest, GenerateResponse
from .templates import TemplateRegistry
from .terminology import Terminology
from .validation import apply_validation


def run_pipeline(
    request: GenerateRequest,
    registry: TemplateRegistry,
    terminology: Terminology,
    rules: ExtractionRules,
    settings: Settings,
    refiner: LLMRefiner | None = None,
) -> GenerateResponse:
    audit: list[AuditEvent] = []
    steps: list[str] = []
    segments = list(request.segments)
    deidentified = False
    corrections: list = []

    if request.options.deidentify:
        segments, findings = deidentify_segments(segments)
        deidentified = True
        steps.append(f"deidentify({len(findings)} hits)")
        audit.append(
            AuditEvent(
                session_id=request.session_id,
                event_type="deidentify",
                payload={"hits": len(findings), "types": sorted({f["type"] for f in findings})},
            )
        )
    else:
        steps.append("deidentify(skipped)")

    if request.options.term_correction and terminology.entries:
        segments, corrections = terminology.correct_segments(segments)
        steps.append(f"term_correction({len(corrections)})")
        if corrections:
            audit.append(
                AuditEvent(
                    session_id=request.session_id,
                    event_type="term_correction",
                    payload={"count": len(corrections), "version": terminology.version},
                )
            )
    else:
        steps.append("term_correction(skipped)")

    # 模板先解析：pool_all_sections 的报告类型（如病理）需要保留未分类句子入池
    template, fallback_note = registry.resolve(request.template_id)
    if fallback_note:
        audit.append(
            AuditEvent(
                session_id=request.session_id,
                event_type="template_fallback",
                payload={"requested": request.template_id, "used": template.id},
            )
        )

    if request.elements is not None:
        elements = list(request.elements)
        steps.append(f"elements(provided:{len(elements)})")
    else:
        elements = extract_elements(
            segments,
            rules,
            keep_unclassified=request.options.keep_unclassified or template.pool_all_sections,
        )
        steps.append(f"extract({len(elements)}, unclassified={unclassified_count(elements)})")
        audit.append(
            AuditEvent(
                session_id=request.session_id,
                event_type="extract",
                payload={
                    "elements": len(elements),
                    "unclassified": unclassified_count(elements),
                    "rules_version": rules.version,
                },
            )
        )

    mode = "rules"
    report = generate_report(
        template=template,
        elements=elements,
        session_id=request.session_id,
        mode=mode,
        fallback_note=fallback_note,
    )

    if request.options.use_llm and refiner is not None and refiner.available:
        suggested, note = refiner.refine(report, elements)
        if suggested:
            for field in report.fields:
                if field.key in suggested:
                    field.ai_value = suggested[field.key]
            mode = "rules+llm"
            report.mode = "rules+llm"  # type: ignore[assignment]
        report.warnings.append(note)
        audit.append(
            AuditEvent(
                session_id=request.session_id,
                event_type="llm_refine",
                payload={"accepted": len(suggested), "model": settings.llm_model},
            )
        )
        steps.append(f"llm_refine({len(suggested)})")
    elif request.options.use_llm:
        steps.append("llm_refine(skipped:unavailable)")

    segment_text = {seg.id: seg.text for seg in segments}
    apply_validation(report, template, segment_text)
    steps.append(f"validate(missing_required={len(report.missing_required)})")

    return GenerateResponse(
        session_id=request.session_id,
        report=report,
        elements=elements,
        corrections=corrections,
        deidentified=deidentified,
        audit=audit,
        pipeline=steps,
    )
