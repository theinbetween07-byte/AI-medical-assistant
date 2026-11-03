"""缺失必填字段的候选建议。

只给「候选」，不自动写入：主诉与评估都属于临床判断，必须由医生确认后采纳（FR-07）。
引擎靠「主訴 / 診斷 / 評估」等口述标签判定字段（见 extraction_rules.json），
自由口述时这些字段常为空；这里从已有内容里提取一个可编辑的起点，减少手工输入。

评估（assessment）**不做自动生成**，只列出病历正文里出现过的病名作为参考线索，
并且跳过否定语境（「否認肺炎」不会被当成肺炎），避免把既往史误写成本次诊断。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_FRAG_SPLIT = re.compile(r"[，,。；;\n]+")
# 口述开头常见的铺垫词，构成主诉时去掉
_LEAD_NOISE = ("病人", "患者", "病者", "本人", "我", "今日", "今天", "昨日", "琴日", "而家")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_CHIEF_MAX = 24
_CHIEF_PARTS = 2
# 引擎用这些词表示「没抽到」，语义上等于空
_EMPTY_MARKS = {"", "—", "-", "未提及", "未涉及", "未提供", "not mentioned", "n/a", "na"}

# 评估线索的取材字段（按优先级）
_ASSESS_SOURCES = (
    "history_of_present_illness",
    "past_medical_history",
    "physical_examination",
    "investigations",
)
# 分句里出现这些词就跳过，避免把「否认/无」当成阳性
_NEGATIONS = ("否認", "否认", "無", "无", "排除", "未見", "未见", "not ", "no ")
# A 段关键词里的元词，不是病名
_META_TERMS = {
    "diagnosis", "impression", "assessment", "suspected", "likely", "consistent with",
    "懷疑", "怀疑", "診斷", "诊断", "印象",
}
_ASSESS_MIN_SCORE = 1.2
_ASSESS_MAX_HINTS = 4

_TERMS_CACHE: list[str] | None = None


def _value(field: dict | None) -> str:
    if not field:
        return ""
    return str(field.get("final_value") or field.get("ai_value") or "").strip()


def _filled(field: dict | None) -> bool:
    return _value(field).lower() not in _EMPTY_MARKS


def _trim_lead(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for word in _LEAD_NOISE:
            if text.startswith(word):
                text = text[len(word):].strip()
                changed = True
    return text.strip("，,、；;。 ")


def chief_candidate(history: str) -> str:
    """从现病史里取前两个片段拼成主诉候选（≤24 字）。"""
    parts: list[str] = []
    for raw in _FRAG_SPLIT.split(history or ""):
        part = _trim_lead(raw.strip())
        if len(part) < 2 or not _CJK.search(part):
            continue
        parts.append(part)
        if len(parts) >= _CHIEF_PARTS:
            break
    if not parts:
        return ""
    candidate = "、".join(parts)
    return candidate[:_CHIEF_MAX].rstrip("、，,；; ")


def assessment_terms() -> list[str]:
    """从引擎的 extraction_rules.json 读 A 段病名关键词（score ≥ 1.2）。"""
    global _TERMS_CACHE
    if _TERMS_CACHE is not None:
        return _TERMS_CACHE
    path = Path(__file__).resolve().parents[1] / "terms" / "extraction_rules.json"
    terms: list[str] = []
    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
        for word, score in (rules.get("keywords", {}).get("A") or {}).items():
            if score < _ASSESS_MIN_SCORE or len(word) < 2 or word.lower() in _META_TERMS:
                continue
            terms.append(word)
    except (OSError, ValueError):
        terms = []
    terms.sort(key=len, reverse=True)  # 长词优先，避免「肺炎」掩盖「間質性肺炎」
    _TERMS_CACHE = terms
    return terms


def assessment_hints(text: str, terms: list[str] | None = None) -> list[str]:
    """列出正文里出现过的病名（跳过否定语境），只作为评估的参考线索。"""
    candidates = assessment_terms() if terms is None else terms
    hints: list[str] = []
    for clause in _FRAG_SPLIT.split(text or ""):
        clause = clause.strip()
        if not clause or any(neg in clause for neg in _NEGATIONS):
            continue
        low = clause.lower()
        for term in candidates:
            if term.lower() in low and term not in hints:
                hints.append(term)
    return hints[:_ASSESS_MAX_HINTS]


def suggest_missing(report: dict | None) -> dict[str, dict]:
    """返回 {字段key: {value, options, note}}，只包含能从报告内推断的字段。"""
    if not report:
        return {}
    fields = {f.get("key"): f for f in report.get("fields") or []}

    suggestion: dict[str, dict] = {}
    chief = fields.get("chief_complaint")
    if not _filled(chief):
        candidate = chief_candidate(_value(fields.get("history_of_present_illness")))
        if candidate:
            suggestion["chief_complaint"] = {
                "value": candidate,
                "note": "由现病史整理，请核对是否符合主诉写法（症状 + 时长）",
            }

    assess = fields.get("assessment")
    if not _filled(assess):
        text = "\uff0c".join(
            _value(fields.get(key)) for key in _ASSESS_SOURCES if _filled(fields.get(key))
        )
        hints = assessment_hints(text)
        if hints:
            suggestion["assessment"] = {
                "value": "",
                "options": hints,
                "note": "文中提到的病名，仅供核对；请确认是否属于本次诊断后再写入",
            }
    return suggestion
