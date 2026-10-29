"""端到端自测：流水线 -> 校审门禁 -> 签署 -> 导出。

运行：.venv\\Scripts\\python.exe -m pytest -q   （在 report-engine 目录下）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.extractor import ExtractionRules, extract_elements
from app.pipeline import run_pipeline
from app.schemas import (
    ExportRequest,
    FieldReviewRequest,
    GenerateOptions,
    GenerateRequest,
    SignRequest,
    TranscriptSegment,
)
from app.service import ReportLocked, ReportService
from app.templates import TemplateRegistry
from app.terminology import Terminology

PROJECT = Path(__file__).resolve().parents[1]

SEGMENTS = [
    TranscriptSegment(id="S-001", start_ms=0, end_ms=9000, lang="mixed", confidence=0.93,
                      text="陳先生今日返嚟覆診，佢話呢三日有喉嚨痛同咳，無發燒。"),
    TranscriptSegment(id="S-002", start_ms=9000, end_ms=17000, lang="en", confidence=0.91,
                      text="On examination, blood pressure 128 over 76, pulse 82, temperature 36.8 degree Celsius."),
    TranscriptSegment(id="S-003", start_ms=17000, end_ms=23000, lang="en", confidence=0.88,
                      text="No known drug allergy. Past history of hypertension."),
    TranscriptSegment(id="S-004", start_ms=23000, end_ms=29000, lang="en", confidence=0.85,
                      text="Impression is upper respiratory tract infection. I will prescribe panadol 500 mg PO TDS for 3 days."),
    TranscriptSegment(id="S-005", start_ms=29000, end_ms=34000, lang="mixed", confidence=0.55,
                      text="建議多休息多飲水，一星期後返嚟覆診。"),
]


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    cfg = Settings()
    cfg.data_dir = tmp_path / "data"
    cfg.templates_dir = PROJECT / "templates"
    cfg.terms_dir = PROJECT / "terms"
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def service(settings: Settings) -> ReportService:
    svc = ReportService(settings)
    yield svc
    svc.store.close()


def _request(**option_overrides) -> GenerateRequest:
    options = GenerateOptions(deidentify=False, term_correction=True, use_llm=False)
    for key, value in option_overrides.items():
        setattr(options, key, value)
    return GenerateRequest(session_id="SES-TEST", template_id="general_soap",
                           segments=SEGMENTS, options=options)


def _partial_request() -> GenerateRequest:
    """只口述了主诉，用于验证"必填缺失阻断签署"（BR-10）。"""
    return GenerateRequest(
        session_id="SES-PARTIAL",
        template_id="general_soap",
        segments=[
            TranscriptSegment(id="S-001", start_ms=0, end_ms=6000, lang="mixed", confidence=0.9,
                              text="病人講呢三日喉嚨痛同咳，無發燒。")
        ],
        options=GenerateOptions(term_correction=True),
    )


# --------------------------------------------------------------------- 抽取
def test_extractor_classifies_and_traces_time(settings: Settings) -> None:
    rules = ExtractionRules.load(settings.terms_dir)
    elements = extract_elements(SEGMENTS, rules)

    assert elements, "至少应抽取出一个要素"
    types = {e.type for e in elements}
    assert types - {"uncategorized"}, "应至少归类出 S/O/A/P 中一类"

    for element in elements:
        assert element.source_segment_ids, "每个要素必须可追溯到来源片段（FR-04）"
        for span in element.sources:
            assert span.end_ms >= span.start_ms, "时间戳必须单调"


# ----------------------------------------------------------------- 端到端
def test_pipeline_generates_report_without_fabrication(settings: Settings) -> None:
    response = run_pipeline(
        _request(term_correction=False),
        registry=TemplateRegistry(settings.templates_dir),
        terminology=Terminology.load(settings.terms_dir),
        rules=ExtractionRules.load(settings.terms_dir),
        settings=settings,
    )
    report = response.report
    source_text = "".join(s.text for s in SEGMENTS)

    assert report.template_id == "general_soap"
    assert report.status == "draft"
    assert report.fields, "报告必须包含模板字段"

    # BR-03：只有来源中出现过的内容才能出现在字段里，未覆盖字段写"未提及"
    for field in report.fields:
        if field.ai_value == "未提及":
            continue
        for chunk in field.ai_value.split("；"):
            chunk = chunk.strip()
            if chunk:
                assert chunk in source_text, f"{field.key} 出现了口述中不存在的内容：{chunk}"

    assert report.field("assessment") is not None
    assert "upper respiratory tract infection" in report.field("assessment").ai_value.lower()
    assert any("deidentify(skipped)" == s or s.startswith("deidentify") for s in response.pipeline)


def test_pipeline_marks_low_confidence_and_missing(settings: Settings) -> None:
    response = run_pipeline(
        _request(),
        registry=TemplateRegistry(settings.templates_dir),
        terminology=Terminology.load(settings.terms_dir),
        rules=ExtractionRules.load(settings.terms_dir),
        settings=settings,
    )
    report = response.report
    # S-005 confidence 0.55 < 0.6 -> 相关字段应带低置信度提示（BR-09）
    flagged = [f.key for f in report.fields if "low_confidence" in f.flags]
    assert flagged, "低置信度片段应产生核对提示"

    # 模板里必填但口述未覆盖的字段必须进入缺失清单（FR-06/BR-10）
    assert isinstance(report.missing_required, list)
    for key in report.missing_required:
        field = report.field(key)
        assert field is not None and "missing" in field.flags


def test_template_fallback_to_general_soap(settings: Settings) -> None:
    response = run_pipeline(
        _request(),
        registry=TemplateRegistry(settings.templates_dir),
        terminology=Terminology.load(settings.terms_dir),
        rules=ExtractionRules.load(settings.terms_dir),
        settings=settings,
    )
    # 不存在的模板 id 走 run_pipeline 时由 registry.resolve 回退（此处直接验证 resolve）
    registry = TemplateRegistry(settings.templates_dir)
    template, note = registry.resolve("not_registered_template")
    assert template.id == "general_soap" and note


# --------------------------------------------------------------- 门禁流程
def test_review_sign_export_gate(service: ReportService) -> None:
    report = service.generate(_partial_request()).report

    # 1) 口述不完整 -> 必填缺失 + 未校审：导出与签署都被拦住（BR-10）
    assert report.missing_required, "未覆盖的必填字段必须进入缺失清单"

    with pytest.raises(ReportLocked):
        service.export(report.id, ExportRequest(formats=["text"]))

    blocked = service.sign(report.id, SignRequest(signed_by="dr-test"))
    assert blocked.ok is False and blocked.blocked_by

    # 2) 必填字段全部采纳（医生确认"未提及"也算完成确认）
    required = [f for f in report.fields if f.required]
    assert required
    for field in required:
        service.review_field(report.id, field.key, FieldReviewRequest(action="accept", reviewer="dr-test"))

    # 3) 校审后 ai_value 保持不可变（AC-07）
    refreshed = service.get_report(report.id)
    assert len(refreshed.reviews) == len(required)
    for review in refreshed.reviews:
        field = refreshed.field(review.field_key)
        assert field is not None and review.ai_value == field.ai_value

    signed = service.sign(report.id, SignRequest(signed_by="dr-test"))
    assert signed.ok is True and signed.report.status == "signed"

    # 4) 导出后仍只读（BR-11）
    records = service.export(report.id, ExportRequest(formats=["text", "json", "pdf"]))
    assert {r.format for r in records} == {"text", "json", "pdf"}
    for record in records:
        path = Path(record.path)
        assert path.is_file() and path.stat().st_size > 0, f"{record.format} 导出失败"

    text = Path(next(r for r in records if r.format == "text").path).read_text(encoding="utf-8")
    assert "主诉" in text

    with pytest.raises(ReportLocked):
        service.review_field(report.id, required[0].key, FieldReviewRequest(action="edit", value="x"))

    assert service.audit_log("SES-PARTIAL"), "应有审计事件"


def test_missing_required_blocks_sign_until_confirmed(service: ReportService) -> None:
    """未确认的必填字段即使被拒绝（reject）也不能签署。"""
    report = service.generate(_partial_request()).report
    required = [f for f in report.fields if f.required]

    for field in required:
        service.review_field(report.id, field.key, FieldReviewRequest(action="reject"))
    still_blocked = service.sign(report.id, SignRequest(signed_by="dr-test"))
    assert still_blocked.ok is False

    for field in required:
        service.review_field(report.id, field.key, FieldReviewRequest(action="accept"))
    assert service.sign(report.id, SignRequest(signed_by="dr-test")).ok is True


def test_edit_and_reject_flow(service: ReportService) -> None:
    report = service.generate(_request()).report
    target = report.fields[0]

    updated = service.review_field(
        report.id, target.key, FieldReviewRequest(action="edit", value="医生修订后的主诉", reviewer="dr-test")
    )
    field = updated.field(target.key)
    assert field.final_value == "医生修订后的主诉"
    assert field.ai_value == target.ai_value, "AI 原值不得被覆盖"
    assert field.effective_value() == "医生修订后的主诉"

    rejected = service.review_field(report.id, target.key, FieldReviewRequest(action="reject"))
    assert rejected.field(target.key).final_value == ""
    assert rejected.field(target.key).effective_value() == target.ai_value


def test_preview_and_store_roundtrip(service: ReportService, settings: Settings) -> None:
    report = service.generate(_request()).report
    assert "general_soap" in (service.preview(report.id, "json"))
    assert service.list_reports(10)
    assert service.health()["status"] == "ok"
    assert service.reload_resources()["templates"]
    assert service.delete_report(report.id) is True
