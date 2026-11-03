"""
Pydantic 數據模型 - API 請求/響應結構
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# 支持的語言代碼
LanguageCode = Literal["yue", "zh", "en", "mix", "auto"]

# 支持的報告模板
ReportTemplate = Literal[
    "discharge_summary",
    "referral_letter",
    "operative_note",
    "consultation_note",
    "progress_note",
    "soap",
]


class HealthResponse(BaseModel):
    """健康檢查響應"""
    ok: bool
    model: str
    device: str
    compute_type: str
    model_loaded: bool


class Segment(BaseModel):
    """單個轉寫段落"""
    id: int
    start: float
    end: float
    text: str


class TranscribeResponse(BaseModel):
    """轉寫響應"""
    language: str
    language_probability: float
    duration: float = Field(..., description="音頻時長（秒）")
    text: str = Field(..., description="完整轉寫文本（每段一行）")
    segments: List[Segment] = Field(default_factory=list)
    elapsed: float = Field(..., description="ASR 耗時（秒）")
    template: str
    model: str


class ErrorResponse(BaseModel):
    """錯誤響應"""
    detail: str
