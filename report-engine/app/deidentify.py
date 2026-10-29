"""规则化去标识化（NFR-04 / 开放问题 Q-04）。

MVP 先用规则列表实现，评估漏检率后再决定是否引入模型识别。
命中的内容替换为占位符，长度与语序尽量保持不变，便于人工核对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DeidentifyResult:
    text: str
    findings: list[dict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.findings)


# 顺序有意义：先长模式（HKID、身份证、MRN），再电话，再邮箱，最后姓名
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("HKID", re.compile(r"\b[A-Z]{1,2}\s?\d{6}\s?\(?\s?[0-9A]\s?\)?")),
    ("ID_CN", re.compile(r"\b\d{17}[\dXx]\b")),
    ("MRN", re.compile(r"(?:MRN|病歷號|病历号|檔案號|档案号|住院號|住院号)\s*[:：]?\s*[A-Za-z0-9\-]{4,}")),
    ("PHONE", re.compile(r"(?:\+\s?852[\s\-]?)?\d{4}[\s\-]?\d{4}\b")),
    ("EMAIL", re.compile(r"[\w.\-+]+@[\w\-]+\.[A-Za-z]{2,}")),
    (
        "NAME_ZH",
        re.compile(
            r"(?:姓名|名字|病人|患者|叫|名叫)\s*[:：]?\s*"
            r"([\u4e00-\u9fff]{2,4})(?=\s*(?:先生|女士|小姐|太太|伯伯|叔叔|婆婆|阿姨|，|,|。|$))"
        ),
    ),
    (
        "NAME_EN",
        re.compile(
            r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b"
        ),
    ),
]


def deidentify(text: str) -> DeidentifyResult:
    """返回替换后的文本与命中清单（用于审计与界面提示）。"""
    findings: list[dict] = []
    masked = text
    counters: dict[str, int] = {}

    for kind, pattern in _PATTERNS:
        def repl(match: re.Match[str], _kind: str = kind) -> str:
            counters[_kind] = counters.get(_kind, 0) + 1
            token = match.group(0)
            findings.append(
                {
                    "type": _kind,
                    "index": counters[_kind],
                    "original_length": len(token),
                }
            )
            return f"[{_kind}_{counters[_kind]}]"

        masked = pattern.sub(repl, masked)

    return DeidentifyResult(text=masked, findings=findings)


def deidentify_segments(segments: list) -> tuple[list, list[dict]]:
    """批量处理转写分段，就地生成新对象（不修改入参）。"""
    findings: list[dict] = []
    out = []
    for seg in segments:
        result = deidentify(seg.text)
        for item in result.findings:
            item["segment_id"] = seg.id
            findings.append(item)
        out.append(seg.model_copy(update={"text": result.text}))
    return out, findings
