"""引擎输出后处理：按语义把落错字段的内容归位。

背景（真实联调 ses_727a31e86d6f）：
    引擎的 O 段（客观）会同时产出「体格检查」与「检查结果」两个字段，但它的归类
    依赖关键词命中顺序，实测「體查體溫38度5」被放进「检查结果」，而「血壓160，95」
    落在「体格检查」。医生看到的是体温跑到化验单里，属于明显的内容错位。

策略（保守，不动算法）：
    只处理 `investigations -> physical_examination` 一个方向；
    片段必须同时满足「含体征词 + 含数字」且「不含化验/影像词」才搬运，
    因此「抽血檢查血常規和肝功能」不会被误搬。
    人工已改过的字段（final_value 非空）一律不动。
"""

from __future__ import annotations

import re

_SECTION_SPLIT = re.compile(r"[；;\n]+")
_DIGIT = re.compile(r"\d")

# 体征词：出现即说明这段是查体记录
VITAL_WORDS = (
    "体温", "體溫", "temp", "血壓", "血压", "blood pressure", "bp",
    "脈搏", "脉搏", "pulse", "心率", "heart rate",
    "呼吸頻率", "呼吸频率", "respiratory rate",
    "血氧", "spo2", "saturation", "°c",
)

# 化验/影像词：出现即说明这段是检查结果，不搬
LAB_WORDS = (
    "血常規", "血常规", "肝功能", "腎功能", "肾功能", "化驗", "化验", "檢驗", "检验",
    "抽血", "驗血", "验血", "blood test", "cbc", "crp", "wbc", "alt", "creatinine",
    "x光", "x-ray", "ct", "mri", "超聲波", "超声波", "ultrasound", "影像",
    "尿常規", "尿常规", "培養", "培养", "culture", "報告", "报告",
)

FIELD_PE = "physical_examination"
FIELD_INV = "investigations"


def _hit(text: str, words: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def is_vital_sign(text: str) -> bool:
    """是否是一段「查体测得的值」——含体征词和数字，且不含化验/影像词。"""
    frag = text.strip()
    if not frag or not _DIGIT.search(frag):
        return False
    if _hit(frag, LAB_WORDS):
        return False
    return _hit(frag, VITAL_WORDS)


def _value(field: dict, key: str) -> str:
    return str(field.get(key) or "").strip()


def _tidy_fragment(frag: str) -> str:
    """去掉片段尾部的逗号/顿号，避免拼接出「160，；體溫…」这种双标点。"""
    return frag.strip().rstrip("，,、;；").strip()


def rebalance_report(report: dict) -> tuple[dict, list[dict]]:
    """把误落在「检查结果」里的体征片段搬到「体格检查」。

    返回 (新报告, 搬运记录)。没有任何搬运时原样返回，调用方可据此跳过审计。
    """
    if not report:
        return report, []
    fields = report.get("fields") or []
    by_key = {f.get("key"): f for f in fields}
    inv, pe = by_key.get(FIELD_INV), by_key.get(FIELD_PE)
    if not inv or not pe:
        return report, []

    # 已经人工改过就不碰（校审结果优先于自动归位）
    if _value(inv, "final_value") or _value(pe, "final_value"):
        return report, []

    source = _value(inv, "ai_value")
    if not source:
        return report, []

    moved: list[str] = []
    kept: list[str] = []
    for frag in _SECTION_SPLIT.split(source):
        frag = _tidy_fragment(frag)
        if not frag:
            continue
        (moved if is_vital_sign(frag) else kept).append(frag)

    if not moved:
        return report, []

    existing = _value(pe, "ai_value")
    pe_parts = (
        [p for p in (_tidy_fragment(x) for x in _SECTION_SPLIT.split(existing)) if p]
        if existing else []
    )
    pe["ai_value"] = "；".join([*pe_parts, *moved])
    inv["ai_value"] = "；".join(kept)

    note = "；已按语义从「检查结果」归位体征：" + "、".join(moved)
    pe["note"] = (pe.get("note") or "") + note
    if not inv["ai_value"]:
        inv["note"] = (inv.get("note") or "") + "；体征已移至体格检查"

    # 必填校验依赖字段内容，搬运后需要重算
    if "missing_required" in report:
        report["missing_required"] = [
            f.get("key") for f in fields
            if f.get("required") and not (_value(f, "final_value") or _value(f, "ai_value"))
        ]

    return report, [{
        "from": FIELD_INV,
        "to": FIELD_PE,
        "fragments": moved,
    }]


SYNC_REVIEWER = "system:rebalance"
# 引擎 final_value 为空会回落到 ai_value，且不接受空值 edit；清空字段时用这个占位
EMPTY_MARK = "—"


def hide_rebalanced_source(report: dict) -> dict:
    """展示层清洗：被归位清空的原字段，不再把旧 `ai_value` 显示出来。

    引擎 `effective_value()` 在 `final_value` 为空时回落 `ai_value`，所以清空只能写
    `final_value = "—"`（或 review 的 reject）。这里把这些字段的 `ai_value` 一并隐藏，
    原文仍可从 reviews 回溯。
    """
    if not report:
        return report
    touched = {
        r.get("field_key")
        for r in report.get("reviews") or []
        if r.get("reviewer") == SYNC_REVIEWER
    }
    if not touched:
        return report
    cleaned = dict(report)
    cleaned["fields"] = [
        {**f, "ai_value": "", "note": f.get("note") or "体征已移至体格检查"}
        if f.get("key") in touched and not str(f.get("final_value") or "").strip().strip(EMPTY_MARK)
        else f
        for f in report.get("fields") or []
    ]
    return cleaned
