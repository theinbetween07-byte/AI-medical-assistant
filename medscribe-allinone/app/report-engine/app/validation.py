"""报告校验（FR-06 / BR-05 / AI-03 幻觉统计）。

三项检查：
  1. 必填缺失：模板必填字段为"未提及" → 进入缺失清单，阻断签署（BR-10）；
  2. 来源可追溯：字段内容词必须能在来源转写片段中找到依据，否则标"待核实"；
  3. 剂量一致性：字段中的剂量/数量表达必须出现在来源中，否则标"待核实"（BR-05）。

规则内核生成的字段天然可追溯；这三项主要为 LLM 增强模式兜底。
"""

from __future__ import annotations

import re

from .schemas import GeneratedReport, ReportField, ReportTemplate

PLACEHOLDER_MISSING = "未提及"
EVIDENCE_MIN_RATIO = 0.75

DOSE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|μg|g|ml|mL|IU|units?|tab(?:let)?s?|capsules?)\b",
    re.IGNORECASE,
)
DURATION_RE = re.compile(r"\d+\s*(?:days?|weeks?|months?|times?|天|日|週|周|月|次)")
LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
HAN_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _content_tokens(text: str) -> set[str]:
    """抽取内容词：英文词（≥3 字母）+ 中文双字滑窗。"""
    tokens: set[str] = {w.lower() for w in LATIN_WORD_RE.findall(text)}
    for run in HAN_RUN_RE.findall(text):
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    return tokens


def _normalize_numeric(text: str) -> set[str]:
    out: set[str] = set()
    for pattern in (DOSE_RE, DURATION_RE):
        for hit in pattern.findall(text):
            out.add(re.sub(r"\s+", "", hit).lower())
    return out


def evidence_ratio(value: str, source_texts: list[str]) -> float:
    tokens = _content_tokens(value)
    if not tokens:
        return 1.0
    source_tokens = _content_tokens(" ".join(source_texts))
    if not source_tokens:
        return 0.0
    hit = sum(1 for t in tokens if t in source_tokens)
    return hit / len(tokens)


def unsupported_numerics(value: str, source_texts: list[str]) -> list[str]:
    """返回字段中出现但来源中不存在的剂量/数量表达（BR-05）。"""
    value_nums = _normalize_numeric(value)
    if not value_nums:
        return []
    source_nums = _normalize_numeric(" ".join(source_texts))
    source_flat = re.sub(r"\s+", "", " ".join(source_texts)).lower()
    missing = []
    for token in sorted(value_nums):
        if token in source_nums or token in source_flat:
            continue
        missing.append(token)
    return missing


def check_field(
    field: ReportField, source_texts: list[str]
) -> tuple[list[str], bool, str]:
    """对单个字段执行检查，返回 (flags, evidence_ok, note)。"""
    flags: list[str] = []
    note = ""

    if not field.ai_value or field.ai_value.strip() in {PLACEHOLDER_MISSING, ""}:
        flags.append("missing")
        return flags, True, note

    ratio = evidence_ratio(field.ai_value, source_texts)
    evidence_ok = ratio >= EVIDENCE_MIN_RATIO
    if not evidence_ok:
        flags.append("unsupported")
        note = f"字段内容与来源转写匹配度 {ratio:.0%}，请人工核实"

    missing_nums = unsupported_numerics(field.ai_value, source_texts)
    if missing_nums:
        flags.append("dose_mismatch")
        note = (note + " " if note else "") + "以下剂量/数量未在口述中出现：" + ", ".join(missing_nums)

    return flags, evidence_ok, note.strip()


def apply_validation(
    report: GeneratedReport,
    template: ReportTemplate,
    segment_text: dict[str, str],
) -> None:
    """就地更新 report 的字段标记、缺失清单与警告。"""
    missing_required: list[str] = []
    warnings: list[str] = list(report.warnings)

    for field in report.fields:
        source_texts = [
            segment_text.get(span.segment_id, span.text) for span in field.sources
        ]
        if not source_texts and field.ai_value and field.ai_value != PLACEHOLDER_MISSING:
            # 无来源却有内容 —— 视为不可追溯
            source_texts = []

        extra_flags, evidence_ok, note = check_field(field, source_texts)
        # 生成阶段已确定的标记保留：低置信度（BR-09）与派生字段（模板拼装，需医生核对）
        existing = [f for f in field.flags if f in {"low_confidence", "derived"}]
        field.flags = sorted(set(existing + extra_flags))
        field.evidence_ok = evidence_ok
        if note:
            field.note = (field.note + " " if field.note else "") + note
        if field.required and "missing" in field.flags:
            missing_required.append(field.key)

    report.missing_required = missing_required
    if missing_required:
        warnings.append(
            "存在未提及的必填字段：" + "、".join(missing_required) + "，签署与导出已被阻断（BR-10）"
        )

    unsupported = [f.key for f in report.fields if "unsupported" in f.flags]
    if unsupported:
        warnings.append("以下字段需人工核实：" + "、".join(unsupported))

    mismatched = [f.key for f in report.fields if "dose_mismatch" in f.flags]
    if mismatched:
        warnings.append("以下字段的剂量与口述不一致：" + "、".join(mismatched))

    report.warnings = warnings
