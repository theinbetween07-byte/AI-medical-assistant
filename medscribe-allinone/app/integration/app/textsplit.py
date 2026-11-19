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

MAX_CHARS = 24          # 中文按字数切：中文没有词间空格，短句才便于规则匹配
LATIN_MAX_CHARS = 80    # 英文/拉丁文按短语切：过短会把 "tumour size" 之类的关键词拆散
MIN_CLAUSE = 6

# 中文句末标点无条件切；英文句号只在后接空白时切，避免把 "3.5 cm" 拆开
_SENT = re.compile(r"(?<=[。！？!?])|(?<=[.])(?=\s)")
_CLAUSE = re.compile(r"(?<=[，、；;])")
_LATIN_CLAUSE = re.compile(r"(?<=[,;，；])\s*")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
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


# 纯单位结尾不能续接下一个词（"6 cm" + "proximal margin" 是两条独立信息）
_UNIT_TAIL = {"cm", "mm", "mg", "ml", "kg", "g", "l", "no"}
# 介词/连词结尾说明句子被分段截断，续接时要补空格而不是拼成一个词
_CONNECTORS = {"of", "to", "in", "on", "and", "or", "with", "for", "the", "a", "at", "by"}


def _is_latin(text: str) -> bool:
    """英文/拉丁文段落：按空格分词，不能套用中文的固定字数硬切。"""
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if not latin:
        return False
    return cjk / max(1, cjk + latin) < 0.2


def _is_continuation(prev: str, cur: str, same_source: bool = False) -> bool:
    """判断 cur 是否是 prev 被 ASR 从中间切断的续片。

    真实语音里 Whisper 常在冒号、逗号停顿处，甚至在单词中间断句：
    "histological tumor grade" / ", moderately differentiated"、
    "relation of tumor to ant" / "erior peritoneum reflection, not applicable"。

    same_source=True 表示两片来自同一个 ASR segment（本模块自己切出来的）：
    此时英文逗号是刻意的短语边界（"tumour size 3.5 cm" / "no perforation"），
    不能再合并，否则字段又会互相抢占内容。
    """
    if not prev or not cur:
        return False
    if re.match(r"^[,，、；;]", cur):
        return True
    if prev.rstrip().endswith(("。", "！", "？", ".", "!", "?")):
        return False
    # 中文逗号是分句符，不合并
    if prev.rstrip().endswith(("，", "、", "；")):
        return False
    if prev.rstrip().endswith((",", ";")) and cur[:1].isascii() and cur[:1].islower():
        return not same_source
    # 跨段的词中断词：ASR 把 "near-complete" 断成 "near-complet" / "e, ..."，
    # 或 "differentiated" 断成 "differentiate" / "d adenocarsin"。
    if not same_source and re.match(r"^[a-z](?=[\s,;，；])", cur):
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
    p, c = prev.rstrip(), cur.lstrip()
    if p.endswith((",", "，", ";", "；", "、")):
        sep = " " if p[-1:].isascii() else ""
        return p + sep + c
    # ASR 词中断词：cur 只剩 1 个字母（"d adenocarsin" / "e, mesorectal"）
    if re.match(r"^[a-z](?=[\s,;，；])", c):
        return p + c
    tail = p.split(" ")[-1]
    if tail.lower() in _CONNECTORS:
        return p + " " + c
    if re.fullmatch(r"[A-Za-z]{1,4}", tail) and tail.lower() not in _UNIT_TAIL:
        return p + c
    # 中英文边界不加空格；英文词与词之间的续片补空格
    if not (p[-1:].isascii() and c[:1].isascii()):
        return p + c
    return p + " " + c


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


def _split_words(part: str, max_chars: int) -> list[str]:
    """超长拉丁短语只在空格处切；找不到空格就整段保留，绝不劈开单词。"""
    out: list[str] = []
    piece = part
    while len(piece) > max_chars * 2:
        cut = piece.rfind(" ", 0, max_chars * 2)
        if cut <= 0:
            break
        out.append(piece[:cut].rstrip())
        piece = piece[cut:].lstrip()
    if piece:
        out.append(piece)
    return out


def _split_latin(part: str, max_chars: int) -> list[str]:
    """英文/病理口述：按逗号、分号切成完整短语，保留关键词（如 tumour size）。"""
    if len(part) <= max_chars:
        return [part]
    pieces = [p.strip() for p in _LATIN_CLAUSE.split(part) if p.strip()]
    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < MIN_CLAUSE:
            merged[-1] = _join(merged[-1], piece)
        else:
            merged.append(piece)
    out: list[str] = []
    for piece in merged:
        out.extend(_split_words(piece, max_chars))
    return out


def split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    text = tidy(text)
    if not text:
        return []
    latin = _is_latin(text)
    limit = LATIN_MAX_CHARS if latin else max_chars
    parts: list[str] = []
    for sent in _SENT.split(text):
        sent = sent.strip()
        if not sent:
            continue
        parts.extend(_split_latin(sent, limit) if latin else _split_long(sent, limit))
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
        if merged and _is_continuation(
            merged[-1][0], text, same_source=merged[-1][3] is seg
        ):
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
