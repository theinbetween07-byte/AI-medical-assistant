"""結直腸病理報告模板自測（templates/pathology_colorectal.json）。

字段口徑對齊使用者提供的報告樣本結構，內容一律用合成數據（NFR-05），
真實樣本圖片僅留在 .deepworks/tmp 供人工比對，不入庫、不進測試。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.extractor import ExtractionRules
from app.main import SAMPLES
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
from app.validation import PLACEHOLDER_MISSING

PROJECT = Path(__file__).resolve().parents[1]
TEMPLATE_ID = "pathology_colorectal"

# synoptic 段（口述为「標籤：值」形式）在样本中的行序，用于切分子集
SYNOPTIC_COUNT = 17

REQUIRED_KEYS = {
    "tumour_site",
    "operative_procedure",
    "tumour_type",
    "tumour_grade",
    "tumour_dimension",
    "perforation",
    "peritoneal_reflection",
    "mesorectal_excision_plane",
    "extent_of_invasion",
    "lymphovascular_invasion",
    "perineural_invasion",
    "lymph_node_status",
    "tumour_deposits",
    "proximal_margin",
    "distal_margin",
    "circumferential_margin",
    "diagnosis",
}

EXPECTED_SYNOPTIC = {
    "case_number": "SP-2026-004521",
    "tumour_site": "sigmoid colon",
    "operative_procedure": "laparoscopic anterior resection",
    "tumour_type": "adenocarcinoma, not otherwise specified",
    "tumour_grade": "moderately differentiated",
    "tumour_dimension": "3.5 x 2.8 x 1.2 cm",
    "perforation": "not identified",
    "peritoneal_reflection": "not applicable",
    "mesorectal_excision_plane": "not applicable",
    "extent_of_invasion": "tumour invades through muscularis propria into subserosa",
    "lymphovascular_invasion": "not identified",
    "perineural_invasion": "present",
    "lymph_node_status": "2 out of 16 lymph nodes show metastatic carcinoma",
    "tumour_deposits": "absent",
    "proximal_margin": "free of tumour, 6.0 cm from tumour",
    "distal_margin": "free of tumour, 4.5 cm from tumour",
    "circumferential_margin": "free of tumour, 1.2 cm",
}


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


def _segments(limit: int | None = None) -> list[TranscriptSegment]:
    segments, _note = SAMPLES[TEMPLATE_ID]
    if limit is not None:
        segments = segments[:limit]
    return [TranscriptSegment(**seg) for seg in segments]


def _request(limit: int | None = None, **option_overrides) -> GenerateRequest:
    options = GenerateOptions(deidentify=False, term_correction=False, use_llm=False)
    for key, value in option_overrides.items():
        setattr(options, key, value)
    return GenerateRequest(
        session_id="SES-PATH",
        template_id=TEMPLATE_ID,
        segments=_segments(limit),
        options=options,
    )


def _generate(settings: Settings, limit: int | None = None, **option_overrides):
    return run_pipeline(
        _request(limit, **option_overrides),
        registry=TemplateRegistry(settings.templates_dir),
        terminology=Terminology.load(settings.terms_dir),
        rules=ExtractionRules.load(settings.terms_dir),
        settings=settings,
    ).report


# ------------------------------------------------------------------ 模板声明
def test_template_registered_and_declares_pooled_fields(settings: Settings) -> None:
    registry = TemplateRegistry(settings.templates_dir)
    assert not registry.errors, registry.errors
    template = registry.get(TEMPLATE_ID)
    assert template is not None, "病理模板必须已注册"

    assert template.pool_all_sections is True, "病理报告跨 S/O/A/P，需整池匹配"
    assert template.field_separator == " ", "叙述段落用空格连接更接近报告原文"
    assert {f.key for f in template.fields if f.required} == REQUIRED_KEYS

    orders = [f.order for f in template.fields]
    assert len(set(orders)) == len(orders), "order 不允许重复（决定报告行序）"
    assert len({f.key for f in template.fields}) == len(template.fields)

    for field in template.fields:
        # 结构化 synoptic 字段禁止兜底：宁可"未提及"也不塞无关内容（BR-03）
        if field.key in REQUIRED_KEYS - {"diagnosis"}:
            assert field.accepts_fallback is False, f"{field.key} 不应兜底"
        assert field.section in {"S", "O", "A", "P", "uncategorized"}


# ------------------------------------------------------------- 标签直配与去标签
def test_synoptic_lines_map_to_fields_and_strip_labels(settings: Settings) -> None:
    report = _generate(settings, limit=SYNOPTIC_COUNT)

    for key, expected in EXPECTED_SYNOPTIC.items():
        field = report.field(key)
        assert field is not None, f"模板缺少字段 {key}"
        assert field.ai_value == expected, f"{key} 期望 {expected!r}，实际 {field.ai_value!r}"
        assert field.sources, f"{key} 必须带来源引用（FR-04）"
        # 标签本体不应出现在值里（值只保留讨论内容）
        assert ":" not in field.ai_value and "：" not in field.ai_value

    # 本子集只含 synoptic 行：诊断段未口述，但模板按已抽取值自动拼了一句（并标"自动生成"）
    diagnosis = report.field("diagnosis")
    assert diagnosis.ai_value.startswith("Sigmoid colon, laparoscopic anterior resection:")
    assert "moderately differentiated adenocarcinoma, not otherwise specified" in diagnosis.ai_value
    assert "tumour invades through muscularis propria into subserosa" in diagnosis.ai_value
    assert "derived" in diagnosis.flags, "派生字段必须提示医生核对"
    assert diagnosis.sources, "派生字段也要带来源引用（拼装来源）"
    assert report.missing_required == [], "自动生成后不再是缺失必填，但医生仍需确认（BR-10）"


def test_derived_diagnosis_needs_required_parts_or_stays_missing(settings: Settings) -> None:
    """只口述肿瘤部位时，诊断句不得凭想象补全 —— 如实"未提及"并阻断签署（BR-03 / BR-10）。"""
    report = _generate(settings, limit=2)  # P-002 只有 Tumour site

    diagnosis = report.field("diagnosis")
    assert diagnosis.ai_value == PLACEHOLDER_MISSING
    assert "tumour_type" in diagnosis.note and "extent_of_invasion" in diagnosis.note
    assert "diagnosis" in report.missing_required


def test_dictated_diagnosis_wins_over_template_compose(settings: Settings) -> None:
    """医生直接口述诊断时，按原话采用，不被模板拼装覆盖（FR-07 原值保留）。"""
    request = _request()
    request.segments.append(
        TranscriptSegment(
            id="P-900",
            start_ms=151000,
            end_ms=160000,
            lang="en",
            confidence=0.9,
            text="Diagnosis: Sigmoid colon, anterior resection: moderately differentiated adenocarcinoma, "
            "invading through the muscularis propria into the subserosa.",
        )
    )
    report = run_pipeline(
        request,
        registry=TemplateRegistry(settings.templates_dir),
        terminology=Terminology.load(settings.terms_dir),
        rules=ExtractionRules.load(settings.terms_dir),
        settings=settings,
    ).report

    diagnosis = report.field("diagnosis")
    assert "invading through the muscularis propria" in diagnosis.ai_value, "应采用口述的诊断原文"
    assert "derived" not in diagnosis.flags, "口述内容不是派生结果"


def test_margin_fields_are_not_cross_contaminated(settings: Settings) -> None:
    report = _generate(settings, limit=SYNOPTIC_COUNT)
    proximal = report.field("proximal_margin").ai_value
    distal = report.field("distal_margin").ai_value
    circumferential = report.field("circumferential_margin").ai_value

    assert proximal.startswith("free of tumour, 6.0")
    assert distal.startswith("free of tumour, 4.5")
    assert circumferential.startswith("free of tumour, 1.2")
    assert "6.0" not in distal and "4.5" not in proximal


# --------------------------------------------------------------- 章节标题作用域
def test_section_headings_route_narrative_and_drop_heading_text(settings: Settings) -> None:
    report = _generate(settings)

    macro = report.field("macroscopic_examination").ai_value
    micro = report.field("microscopic_examination").ai_value
    diagnosis = report.field("diagnosis").ai_value

    # 标题句本身不进字段（否则报告里会出现孤立的 "Macroscopic examination"）
    for value in (macro, micro, diagnosis):
        assert value != PLACEHOLDER_MISSING
        assert "examination." not in value.lower()

    assert "segment of sigmoid colon measuring 18.0 cm" in macro
    assert "moderate nuclear pleomorphism" in micro
    assert "muscularis propria" not in macro, "镜下描述不应混进宏观检查"

    # 医生未口述诊断原文时，DIAGNOSIS 段由模板按已抽取值拼装
    assert diagnosis.lower().startswith("sigmoid colon, laparoscopic anterior resection")
    assert "derived" in report.field("diagnosis").flags
    assert macro.count("specimen") == 1

    # 宏觀/鏡下段落的來源時間戳必須落在各自標題之後（NFR-12 可追溯）
    heading_ms = 86000
    assert all(span.start_ms >= heading_ms for span in report.field("macroscopic_examination").sources)


def test_uncategorized_sentences_are_pooled_for_pathology(settings: Settings) -> None:
    """病理模板 pool_all_sections=True：即使宿主關掉 keep_unclassified 也不能丢句子。"""
    report = _generate(settings, keep_unclassified=False)
    macro = report.field("macroscopic_examination").ai_value
    assert "serosal surface is not involved" in macro
    assert "mesocolic fat" in macro


# ------------------------------------------------------------ 结构化字段不猜内容
def test_structured_fields_stay_missing_without_dictation(settings: Settings) -> None:
    """只口述了肿瘤部位：其余必填项必须写"未提及"并进入缺失清单（BR-03 / BR-10）。"""
    report = _generate(settings, limit=2)

    assert report.field("tumour_site").ai_value == "sigmoid colon"
    for key in REQUIRED_KEYS - {"tumour_site"}:
        assert report.field(key).ai_value == PLACEHOLDER_MISSING, f"{key} 不应被其他内容填充"

    assert set(report.missing_required) == REQUIRED_KEYS - {"tumour_site"}
    assert any("BR-10" in w for w in report.warnings)


def test_english_export_localizes_header_and_placeholder(service: ReportService) -> None:
    """英文模板导出不应混入中文表头/占位符（BR-03 仍写"未提及"，但渲染为 Not mentioned）。"""
    report = service.generate(_request(limit=2)).report
    assert report.field("diagnosis").ai_value == PLACEHOLDER_MISSING

    text = service.preview(report.id, "text", "en")
    assert "Template v1.0.0" in text and "Status draft" in text
    assert "Not mentioned" in text

    body = text.split("\nNotes:", 1)[0]  # 告警文案暂未双语，只校验报告正文
    assert "未提及" not in body
    assert "未提及" not in body.split("-" * 60, 1)[0], "英文表头不得混入中文"

    zh_like = service.preview(report.id, "text", "bilingual")
    assert "未提及" in zh_like, "内部占位符保持不变，供门禁与中文报告使用"


def test_pathology_flow_sign_and_export(service: ReportService) -> None:
    report = service.generate(_request()).report

    assert report.template_id == TEMPLATE_ID
    assert report.missing_required == [], report.missing_required

    # 未校审前不得签署/导出（BR-10）
    blocked = service.sign(report.id, SignRequest(signed_by="dr-path"))
    assert blocked.ok is False
    with pytest.raises(ReportLocked):
        service.export(report.id, ExportRequest(formats=["text"]))

    for field in [f for f in report.fields if f.required]:
        service.review_field(
            report.id, field.key, FieldReviewRequest(action="accept", reviewer="dr-path")
        )

    signed = service.sign(report.id, SignRequest(signed_by="dr-path"))
    assert signed.ok is True

    records = service.export(
        report.id, ExportRequest(formats=["text", "json", "pdf"], language_mode="en")
    )
    assert {r.format for r in records} == {"text", "json", "pdf"}

    text = Path(next(r for r in records if r.format == "text").path).read_text(encoding="utf-8")
    # 英文模式下报告行样式与样本一致：`Label: value`
    assert "Tumour site: sigmoid colon" in text
    assert "Lymph node status: 2 out of 16 lymph nodes show metastatic carcinoma" in text
    assert "Circumferential margin: free of tumour, 1.2 cm" in text
    assert "MACROSCOPIC EXAMINATION: The specimen consists of" in text

    # 导出后只读（BR-11）
    with pytest.raises(ReportLocked):
        service.review_field(
            report.id, report.fields[0].key, FieldReviewRequest(action="edit", value="x")
        )
