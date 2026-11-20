"""粵語臨床口述的同音字/近音字修正（保守表）。

为什么需要：
    Whisper large-v3 在粵語上会把临床高频词写成同音字，例如
    「闌尾炎 → 爛尾炎」「咳嗽 → 咳嗅」「黃痰 → 黃談」「胸片 → 胸腱」。
    这些不是口音问题，而是粤语同音字选择错误，词典级别就能修好。

为什么保守：
    只收**多字、临床唯一**的组合，且左侧写法在病历中不成立或几乎不出现；
    单字（如「仲/還」「嗰/果」）和本身合法的词（「複合」「查訪」单独出现）
    一律不动，避免把正常文本改坏。修正只作用于 zh / yue 片段。

语料依据：hk-clinical-synthetic 300 条粤英口述（TTS 逐字稿）对比，
          修正规则同时通过 tests/test_integration.py::CantoneseFixTest 校验。
"""

from __future__ import annotations

# (错误写法, 正确写法)
RULES: tuple[tuple[str, str], ...] = (
    ("爛尾炎", "闌尾炎"),
    ("咳嗅", "咳嗽"),
    ("黃談", "黃痰"),
    ("咳談", "咳痰"),
    ("胸腱", "胸片"),
    ("換解", "緩解"),
    ("鬥性心律", "竇性心律"),
    ("複合培養", "覆核培養"),
    ("心搞", "心絞"),
    ("又腳", "右腳"),
    ("又側", "右側"),
    ("又手", "右手"),
    ("記錄今次查訪", "記錄今次查房"),
    ("今次查訪", "今次查房"),
    ("醫足", "醫囑"),
    ("右手冇指麻", "右手無名指麻"),
    ("左手冇指麻", "左手無名指麻"),
)

# 长串优先，避免短规则先命中把长串拆坏（"今次查訪" 是 "記錄今次查訪" 的后缀）
_ORDERED = tuple(sorted(RULES, key=lambda p: len(p[0]), reverse=True))


def fix_cantonese_clinical(text: str) -> str:
    """按保守规则修正粤语临床口述的同音字；无命中时原样返回。"""
    if not text:
        return text
    for wrong, right in _ORDERED:
        if wrong in text:
            text = text.replace(wrong, right)
    return text
