"""集成层数据模型。

与《技术设计》第 3 节实体一一对应：Session / TranscriptSegment / ClinicalElement /
GeneratedReport / FieldReview / ExportRecord / AuditEvent。

报告与要素的"深结构"（字段、来源引用）仍以报告引擎的 JSON 为准，
集成层只做透传与缓存，避免两边 schema 各自漂移。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

LangTag = Literal["zh-yue", "zh", "en", "mixed", "unknown"]
SessionStatus = Literal[
    "created",      # 已建会话，未录音
    "recorded",     # 音频已落盘并通过校验
    "transcribed",  # 已转写
    "corrected",    # 已跑术语纠正
    "extracted",    # 已抽取 S/O/A/P
    "generated",    # 已生成初稿
    "signed",       # 已签署
    "exported",     # 已导出
    "deleted",
]
FieldAction = Literal["pending", "accept", "edit", "reject"]
ExportFormat = Literal["text", "json", "pdf", "html"]
LanguageMode = Literal["en", "zh", "bilingual"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Segment(BaseModel):
    """转写分段（FR-02 / 技术设计 3.1 TranscriptSegment）。"""

    id: str
    session_id: str = ""
    index: int = 0
    start_ms: int = 0
    end_ms: int = 0
    text: str = ""
    text_raw: Optional[str] = None
    lang: LangTag = "unknown"
    confidence: float = 1.0
    confidence_source: str = "asr-global"  # asr-global | asr-segment | manual
    low_confidence: bool = False
    confirmed: bool = False      # 医生是否已核对低置信度片段（BR-09）
    edited: bool = False

    def as_engine_segment(self) -> dict:
        """转换为报告引擎 /api/generate 需要的结构。"""
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "text_raw": self.text_raw,
            "lang": self.lang,
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
        }


class Session(BaseModel):
    id: str
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)
    status: SessionStatus = "created"
    title: str = ""
    audio_filename: str = ""
    audio_path: str = ""          # 相对 data/audio 的文件名
    duration_ms: int = 0
    language: str = ""
    language_probability: float = 0.0
    template_id: str = ""
    report_id: Optional[str] = None
    segment_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    deleted_at: Optional[str] = None
    # 派生字段：列表页用 signed / draft / none 区分「签没签」，不落库
    report_status: str = ""


class SessionDetail(BaseModel):
    session: Session
    segments: list[Segment] = Field(default_factory=list)
    elements: list[dict] = Field(default_factory=list)
    corrections: list[dict] = Field(default_factory=list)
    report: Optional[dict] = None
    exports: list[dict] = Field(default_factory=list)
    suggestions: dict[str, dict] = Field(default_factory=dict)


class AudioValidation(BaseModel):
    """FR-01 / BR-02 的可观察结果。"""

    ok: bool
    duration_ms: int = 0
    rms: float = 0.0
    silent_ratio: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stored_name: str = ""


class AuditEvent(BaseModel):
    session_id: str = ""
    event_type: str
    payload: dict = Field(default_factory=dict)
    at: str = Field(default_factory=utcnow_iso)


# --------------------------------------------------------------------------
# 请求模型
# --------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    template_id: Optional[str] = None
    title: str = ""


class TranscribeOptions(BaseModel):
    language: str = ""            # 空 = auto；yue / zh / en
    beam_size: int = 5
    asr_template: str = "soap"    # 上游 ASR 的模板参数，仅用于日志分组
    confirm_low_confidence: bool = False


class SegmentPatch(BaseModel):
    text: Optional[str] = None
    confirmed: Optional[bool] = None


class ModelSwitchRequest(BaseModel):
    """切换上游 Whisper 型号；与 GET /api/asr/models 的 supported[].id 对齐。"""

    model: str = ""


class CorrectTermsOptions(BaseModel):
    version: Optional[str] = None  # 指定术语库版本可复现（BR-04）


class ExtractOptions(BaseModel):
    deidentify: bool = False
    language_mode: LanguageMode = "bilingual"


class GenerateOptions(BaseModel):
    template_id: Optional[str] = None
    deidentify: bool = False
    language_mode: LanguageMode = "bilingual"
    keep_unclassified: bool = True


class ReviewRequest(BaseModel):
    action: FieldAction
    value: Optional[str] = None
    reviewer: str = "demo-doctor"


class SignRequest(BaseModel):
    signed_by: str = "demo-doctor"


class ExportRequest(BaseModel):
    formats: list[ExportFormat] = Field(default_factory=lambda: ["text", "json", "pdf"])
    language_mode: LanguageMode = "bilingual"


class HealthComponent(BaseModel):
    name: str
    url: str
    ok: bool
    detail: dict = Field(default_factory=dict)
    error: str = ""
