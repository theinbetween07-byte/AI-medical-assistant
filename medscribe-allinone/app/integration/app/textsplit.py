"""转写分段细化：把 ASR 的长段落切成句/短句级片段。

为什么需要：
    Whisper 在没有长停顿时会把整段话输出成**一个** segment（实测 25 秒粤语门诊独白
    = 1 段、70+ 字、0 个句号）。报告引擎的要素抽取按句处理，整段只能落到一个字段
    （实测全部塞进「检查结果」，主诉/计划全空）。切成短句后引擎才能按句归类。

规则：
    1. 先在句末标点（。！？!?）处切；
    2. 单句仍超过 `max_chars` 时，再在逗号/顿号/分号处切，过短碎片并入前一句；
    3. 时间戳按字数比例分摊，保证「点句子跳音频」大致对齐；
    4. 全角/小号标点统一成中文标点（ASR 常输出 U+FE50「﹐」）。
"""

from __future__ import annotations

import re

MAX_CHARS = 24
MIN_CLAUSE = 6

_SENT = re.compile(r"(?<=[。！？!?])")
_CLAUSE = re.compile(r"(?<=[，、；;])")
_PUNCT_MAP = {
    "\ufe50": "，",  # ﹐ small comma
    "\ufe51": "、",  # ﹑ small ideographic comma
    "\ufe54": "；",  # ﹔
    "\ufe56": "？",  # ﹖
    "\ufe55": "：",  # ﹕
}


def tidy(text: str) -> str:
    for src, dst in _PUNCT_MAP.items():
        text = text.replace(src, dst)
    return text.strip()


_UNIT_TAIL = {"cm", "mm", "mg", "ml", "kg", "g", "l", "no", "on", "in", "of", "to"}


def _is_continuation(prev: str, cur: str) -> bool:
    """判断 cur 是否是 prev 被 ASR 从中间切断的续片。

    真实语音里 Whisper 常在冒号、逗号停顿处，甚至在单词中间断句：
    "histological tumor grade" / ", moderately differentiated"、
    "relation of tumor to ant" / "erior peritoneum reflection, not applicable"。
    这些碎片如果各自成为要素，字段只能拿到标签或半个词。
    """
    if not prev or not cur:
        return False
    if re.match(r"^[,，、；;]", cur):
        return True
    if prev.rstrip().endswith(("。", "！", "？", ".", "!", "?")):
        return False
    # 中文逗号是分句符，不合并；英文半角逗号后接小写才是续接
    if prev.rstrip().endswith(("，", "、", "；")):
        return False
    if prev.rstrip().endswith((",", ";")) and cur[:1].isascii() and cur[:1].islower():
        return True
    if len(prev) + len(cur) > 140:
        return False
    if not (cur[0].isascii() and cur[0].islower()):
        return False
    tail = prev.rstrip().split(" ")[-1]
    if not re.fullmatch(r"[A-Za-z]{1,3}", tail):
        return False
    # "6 cm" / "0 of 23" 这类完整短词不是断词，避免把下一条标签并进来
    return tail.lower() not in _UNIT_TAIL


def _join(prev: str, cur: str) -> str:
    """断词直接拼接，逗号后补空格；中文不加空格。"""
    tail = prev.rstrip().split(" ")[-1]
    if re.fullmatch(r"[A-Za-z]{1,4}", tail) and tail.lower() not in _UNIT_TAIL:
        return prev + cur
    if prev.rstrip().endswith((",", "，", ";", "；", "、")):
        sep = " " if prev.rstrip()[-1:].isascii() else ""
        return prev.rstrip() + sep + cur.lstrip()
    return prev + cur


def _split_long(part: str, max_chars: int) -> list[str]:
    if len(part) <= max_chars:
        return [part]
    pieces = [p for p in _CLAUSE.split(part) if p.strip()]
    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < MIN_CLAUSE:
            merged[-1] += piece
        else:
            merged.append(piece)
    # 逗号也切不开的超长句，按词边界硬切，避免把单词劈成两半（anterior -> ant|erior）
    out: list[str] = []
    for piece in merged:
        while len(piece) > max_chars * 2:
            cut = piece.rfind(" ", 0, max_chars)
            if cut < max_chars // 2:
                cut = max_chars  # 中文等无空格文本：按字数切
            out.append(piece[:cut].rstrip())
            piece = piece[cut:].lstrip()
        out.append(piece)
    return out


def split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    text = tidy(text)
    if not text:
        return []
    parts: list[str] = []
    for sent in _SENT.split(text):
        sent = sent.strip()
        if sent:
            parts.extend(_split_long(sent, max_chars))
    return parts


def refine_segments(segments: list[dict], max_chars: int = MAX_CHARS) -> list[dict]:
    """把长 segment 切成短句片段，重新编号 S-001…并分摊时间戳。

    切分后先做一次跨片段重组：把 ASR 在词/短语中间切断的续片接回前一片段，
    再统一编号，避免字段只拿到标签或半个词。
    """
    pieces: list[tuple[str, int, int, dict]] = []
    for seg in segments:
        parts = split_text(seg.get("text", ""), max_chars)
        if not parts:
            continue
        total = sum(len(p) for p in parts) or 1
        start, end = int(seg.get("start_ms", 0)), int(seg.get("end_ms", 0))
        span = max(end - start, 0)
        cursor = start
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            dur = span if last else int(round(span * len(part) / total))
            pieces.append((part, cursor, end if last else min(end, cursor + dur), seg))
            cursor = pieces[-1][2]

    merged: list[tuple[str, int, int, dict]] = []
    for text, start, end, seg in pieces:
        if merged and _is_continuation(merged[-1][0], text):
            merged[-1] = (_join(merged[-1][0], text), merged[-1][1], end, merged[-1][3])
        else:
            merged.append((text, start, end, seg))

    out: list[dict] = []
    for text, start, end, seg in merged:
        item = dict(seg)
        item["id"] = f"S-{len(out) + 1:03d}"
        item["index"] = len(out)
        item["start_ms"] = start
        item["end_ms"] = end
        item["text"] = text
        out.append(item)
    return out
