"""数据模型 —— 对齐《技术设计》第 3 节实体定义。

所有模型均可 JSON 序列化，直接用于 HTTP 接口与导出文件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

LangTag = Literal["zh-yue", "zh", "en", "mixed", "unknown"]
ElementType = Literal["S", "O", "A", "P", "uncategorized"]
SectionType = Literal["S", "O", "A", "P", "uncategorized"]
ElementState = Literal["pending", "confirmed", "edited", "rejected"]
FieldAction = Literal["pending", "accept", "edit", "reject"]
ReportStatus = Literal["draft", "signed"]
ExportFormat = Literal["text", "json", "pdf", "html"]
LanguageMode = Literal["en", "zh", "bilingual"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SourceSpan(BaseModel):
    """要素/字段与原始音频片段的对应关系（NFR-12 可追溯性）。"""

    segment_id: str
    start_ms: int = 0
    end_ms: int = 0
    text: str = ""


class TranscriptSegment(BaseModel):
    """ASR 输出的单个分段，由上游语言识别模块提供。"""

    id: str
    start_ms: int = 0
    end_ms: int = 0
    text: str
    text_raw: Optional[str] = None
    lang: LangTag = "unknown"
    confidence: float = 1.0
    low_confidence: bool = False


class TermCorrection(BaseModel):
    """术语纠正记录，保留可撤销信息（FR-03 / BR-04）。"""

    segment_id: str
    before: str
    after: str
    rule: Literal["exact", "fuzzy"] = "exact"
    term_id: str = ""
    reversible: bool = True


class ClinicalElement(BaseModel):
    """S/O/A/P 临床要素，必须携带来源引用（FR-04）。"""

    id: str
    type: ElementType = "uncategorized"
    text: str
    source_segment_ids: list[str] = Field(default_factory=list)
    sources: list[SourceSpan] = Field(default_factory=list)
    state: ElementState = "pending"
    score: float = 0.0
    matched_rules: list[str] = Field(default_factory=list)


class ComposeSpec(BaseModel):
    """派生字段的拼装规则（如病理报告的 DIAGNOSIS 段）。

    只在字段本身没有被口述/粘贴覆盖时生效：用其他字段的**已抽取值**拼出一句话，
    不引入任何新事实，且标记为"自动生成，请核对"，医生仍需采纳/编辑后才能签署（BR-10）。
    `required_keys` 中任一字段为"未提及"时放弃拼装，如实留占位符并说明缺哪一项（BR-03）。
    """

    pattern: str = ""
    required_keys: list[str] = Field(default_factory=list)


class TemplateField(BaseModel):
    """模板字段定义（声明式，新增模板只加 JSON 文件）。"""

    key: str
    label: str
    section: SectionType = "uncategorized"
    required: bool = False
    order: int = 0
    type: Literal["text", "list", "date", "medication", "numeric"] = "text"
    keywords: list[str] = Field(default_factory=list)
    hint: str = ""
    placeholder_missing: str = "未提及"
    # 章节标题词：口述中出现该标题后，后续句子默认归入本字段（如 MACROSCOPIC EXAMINATION）
    scope_headings: list[str] = Field(default_factory=list)
    # 关键词全未命中时，是否允许按模板顺序兜底领取一条内容。
    # 结构化字段（如病理 synoptic 项）应设为 false：宁可显示"未提及"，也不塞入无关内容（BR-03）
    accepts_fallback: bool = True
    # 派生字段：本字段缺内容时，按 pattern 用其他字段的值拼装一句话（如 DIAGNOSIS）
    compose: ComposeSpec | None = None


class ReportTemplate(BaseModel):
    id: str
    name: str
    specialty: str = "general"
    version: str = "1.0.0"
    language: str = "en"
    description: str = ""
    fields: list[TemplateField] = Field(default_factory=list)
    # true 时不再按 S/O/A/P 分区池化：所有要素进入同一个池参与匹配
    # （用于病理报告这类"章节靠标题划分、内容天然跨 S/O/A/P"的报告类型）
    pool_all_sections: bool = False
    # 同一字段由多句话拼成时的连接符（SOAP 用"；"，病理叙述段落用空格更接近报告原文）
    field_separator: str = "；"

    def sorted_fields(self) -> list[TemplateField]:
        return sorted(self.fields, key=lambda f: (f.order, f.key))


class ReportField(BaseModel):
    """报告中的单个字段（FR-05/FR-06）。"""

    key: str
    label: str
    section: SectionType = "uncategorized"
    required: bool = False
    order: int = 0
    type: str = "text"
    ai_value: str = ""          # AI/规则内核原始值，校审后不可变（AC-07）
    final_value: str = ""       # 医生校审后的生效值，为空时回落到 ai_value
    sources: list[SourceSpan] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    evidence_ok: bool = True
    note: str = ""

    def effective_value(self) -> str:
        return self.final_value if self.final_value.strip() else self.ai_value


class FieldReview(BaseModel):
    """字段级校审记录，ai_value 不可变以便回溯差异（FR-07 / AC-07）。"""

    report_id: str
    field_key: str
    ai_value: str = ""
    final_value: str = ""
    action: FieldAction = "pending"
    reviewed_at: Optional[str] = None
    reviewer: str = "demo-doctor"


class ExportRecord(BaseModel):
    report_id: str
    format: ExportFormat
    path: str = ""
    exported_at: str = Field(default_factory=utcnow_iso)


class AuditEvent(BaseModel):
    session_id: str = ""
    event_type: str
    payload: dict = Field(default_factory=dict)
    at: str = Field(default_factory=utcnow_iso)


class GeneratedReport(BaseModel):
    id: str
    session_id: str = ""
    template_id: str
    template_name: str = ""
    template_version: str = ""
    status: ReportStatus = "draft"
    created_at: str = Field(default_factory=utcnow_iso)
    signed_at: Optional[str] = None
    signed_by: Optional[str] = None
    fields: list[ReportField] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mode: Literal["rules", "llm", "rules+llm"] = "rules"
    reviews: list[FieldReview] = Field(default_factory=list)

    def field(self, key: str) -> Optional[ReportField]:
        for f in self.fields:
            if f.key == key:
                return f
        return None


# --------------------------------------------------------------------------
# 请求 / 响应模型
# --------------------------------------------------------------------------


class GenerateOptions(BaseModel):
    deidentify: bool = False
    term_correction: bool = True
    use_llm: bool = False
    language_mode: LanguageMode = "bilingual"
    keep_unclassified: bool = True


class GenerateRequest(BaseModel):
    """唯一主入口：带时间戳的转写文本进，结构化报告初稿出。"""

    session_id: str = ""
    template_id: str = "general_soap"
    segments: list[TranscriptSegment]
    elements: Optional[list[ClinicalElement]] = None  # 上游已抽取时可直传（跳过抽取）
    options: GenerateOptions = Field(default_factory=GenerateOptions)


class GenerateResponse(BaseModel):
    session_id: str
    report: GeneratedReport
    elements: list[ClinicalElement] = Field(default_factory=list)
    corrections: list[TermCorrection] = Field(default_factory=list)
    deidentified: bool = False
    audit: list[AuditEvent] = Field(default_factory=list)
    pipeline: list[str] = Field(default_factory=list)


class FieldReviewRequest(BaseModel):
    action: FieldAction
    value: Optional[str] = None
    reviewer: str = "demo-doctor"


class SignRequest(BaseModel):
    signed_by: str = "demo-doctor"


class SignResponse(BaseModel):
    ok: bool
    report: Optional[GeneratedReport] = None
    blocked_by: list[str] = Field(default_factory=list)
    message: str = ""


class ExportRequest(BaseModel):
    formats: list[ExportFormat] = Field(default_factory=lambda: ["text", "json"])
    language_mode: LanguageMode = "bilingual"
    fields: Optional[dict[str, str]] = None  # 未经校审流程时的直接取值（可选）
