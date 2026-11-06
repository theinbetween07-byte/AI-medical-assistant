"""术语库：xls 导入 + 保守别名策略 + 与引擎一致的纠正算法（FR-03 / BR-04）。

为什么集成层自己实现一遍纠正：
报告引擎的 /api/generate 会在内部完成纠正，但 PRD 要求独立的
`POST /sessions/{id}/correct-terms` 接口，且替换要"可见、可一键撤销"。
因此这里先做纠正并把记录落库，再把 `term_correction=False` 传给引擎，
避免同一段文本被纠正两次。算法与引擎 v0.1.0 的 terminology.py 保持一致：
    精确匹配（长别名优先） > 模糊匹配（编辑距离 ≤ 2，仅词长 ≥ 5） > 原样保留

为什么不全量导入 602 条：
引擎对非 ASCII 别名做的是无词边界的子串替换。原表里大量泛词（今天/有/没有/轻度）
一旦参与替换会把转写文本改得不可读，直接破坏 AC-04 的"不丢失内容"。
所以按"保守策略"只把**确定性强**的别名交给自动替换，其余保留为词表供查阅。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

from .tc_sc import to_simplified

# ---------------------------------------------------------------------------
# 术语类型码 -> 引擎 category（drug / disease / exam / abbr）
# ---------------------------------------------------------------------------
CATEGORY_MAP: dict[str, str] = {
    "DRU": "drug",
    "DIS": "disease",
    "TES": "exam",
    "LAB": "exam",
    "ANA": "abbr",
    "SYM": "abbr",
    "SGN": "abbr",
    "PRO": "abbr",
    "TME": "abbr",
    "DEP": "abbr",
    "SEV": "abbr",
    "DOS": "abbr",
    "DOC": "abbr",
    "HIS": "abbr",
    "NEG": "abbr",
    "UNC": "abbr",
    "ROL": "abbr",
}

# 不参与自动替换的类型：时间/程度/否定/疑似/文书/角色/科室/给药途径
# 这些词在句子中是语法成分，替换成英文会破坏可读性且无诊断价值。
TYPE_NOT_AUTO = {"TME", "SEV", "NEG", "UNC", "DOC", "ROL", "DEP", "DOS", "HIS"}

# 泛词黑名单：即便类型允许，命中这些词也不自动替换
WORD_STOPLIST = {
    "今天", "今日", "明天", "明日", "昨天", "昨日", "现在", "目前", "最近",
    "之前", "之后", "平时", "一般", "有时", "经常", "偶尔", "一起", "一下",
    "有", "没有", "无", "是", "不是", "可以", "需要", "应该", "可能", "大概",
    "左右", "一些", "有点", "轻度", "中度", "重度", "轻微", "严重", "明显",
    "病人", "患者", "医生", "护士", "医院", "情况", "问题", "时候", "地方",
}

_ALIAS_SPLIT = re.compile(r"[／/、,，;；|]+")
# 模糊兜底只对 4 字及以上词生效（4 字词只容忍 1 个字符差异，5 字以上容忍 2 个）：
# 3 字以内差异 1 就意味着 33% 不同，跨概念误替换风险过高。
_MIN_FUZZY_LEN = 4
_MAX_FUZZY_DIST = 2


def _fuzzy_limit(length: int) -> int:
    return 1 if length < 6 else _MAX_FUZZY_DIST
_TOKEN_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_ASCII_ALIAS = re.compile(r"[A-Za-z0-9 .\-]+")
_CJK_OR_MIX = re.compile(r"[\u4e00-\u9fff]")


def normalize(text: str) -> str:
    """匹配用归一化：NFKC + 繁→简折叠 + 小写。

    折叠只用于索引比对，原文不会被改写（见 tc_sc 的说明）。
    """
    return to_simplified(unicodedata.normalize("NFKC", text).strip().lower())


def fold(text: str) -> str:
    """长度守恒的折叠（配合 normalize 使用，用于按下标回填替换）。"""
    return to_simplified(text).lower()


# ---------------------------------------------------------------------------
# xls 读取
# ---------------------------------------------------------------------------
HEADER_FIELDS = [
    "term_id", "category", "zh", "en", "yue", "jyutping", "type_code", "synonyms", "note",
]


def read_xls(path: Path) -> list[dict]:
    """读取术语库 xls。xlrd 为可选依赖，缺失时给出明确指引。"""
    try:
        import xlrd  # type: ignore
    except ImportError as exc:  # pragma: no cover - 部署环境相关
        raise RuntimeError(
            "读取 .xls 需要 xlrd。请执行："
            "python -m pip install xlrd --target <目录> 并把它加入 sys.path"
        ) from exc

    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    header = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]

    entries: list[dict] = []
    for r in range(1, sheet.nrows):
        row: dict[str, Any] = {}
        for c, name in enumerate(header):
            raw = sheet.cell_value(r, c)
            if isinstance(raw, float) and raw == int(raw):
                raw = int(raw)
            row[name] = str(raw).strip()
        if not row.get("术语ID"):
            continue
        entries.append(
            {
                "term_id": row.get("术语ID", ""),
                "category": row.get("分类", ""),
                "zh": row.get("中文标准名", ""),
                "en": row.get("英文", ""),
                "yue": row.get("粤语口语表达", ""),
                "jyutping": row.get("粤拼", ""),
                "type_code": row.get("术语类型", ""),
                "synonyms": row.get("同义/变体", ""),
                "note": row.get("备注", ""),
            }
        )
    return entries


# ---------------------------------------------------------------------------
# 保守策略：把 xls 条目编译成引擎可用词表 + 完整词表
# ---------------------------------------------------------------------------
def _split_aliases(value: str) -> list[str]:
    return [part.strip() for part in _ALIAS_SPLIT.split(value or "") if part.strip()]


def _alias_allowed(alias: str) -> tuple[bool, str]:
    if not alias:
        return False, "empty"
    if len(alias) < 2:
        return False, "too_short"
    if not _CJK_OR_MIX.search(alias) and len(alias) < 3:
        return False, "too_short_ascii"
    if alias in WORD_STOPLIST:
        return False, "stoplist"
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", alias):
        return False, "no_content"
    return True, ""


def compile_terms(entries: Iterable[dict], version: str) -> tuple[dict, list[dict]]:
    """返回 (引擎词表, 完整词表)。完整词表带 auto_replace / skip_reason 便于审计。"""
    engine_entries: list[dict] = []
    glossary: list[dict] = []
    # 先收集所有候选别名，用于冲突检测
    candidates: list[tuple[dict, str]] = []
    prepared: list[dict] = []

    for entry in entries:
        term_norm = (entry.get("en") or "").strip()
        type_code = (entry.get("type_code") or "").strip().upper()
        category = CATEGORY_MAP.get(type_code, "abbr")

        aliases: list[str] = []
        skipped: list[tuple[str, str]] = []

        if not term_norm:
            skipped.append(("", "no_english"))
        elif type_code in TYPE_NOT_AUTO:
            skipped.append(("", f"type_not_auto:{type_code}"))

        if term_norm and type_code not in TYPE_NOT_AUTO:
            for raw in [entry.get("zh", ""), *(_split_aliases(entry.get("yue", ""))),
                        *(_split_aliases(entry.get("synonyms", "")))]:
                alias = (raw or "").strip()
                if not alias:
                    continue
                if normalize(alias) == normalize(term_norm):
                    continue
                ok, reason = _alias_allowed(alias)
                if not ok:
                    skipped.append((alias, reason))
                    continue
                if alias not in aliases:
                    aliases.append(alias)

        prepared.append(
            {
                "id": (entry.get("term_id") or "").lower() or f"term-{len(prepared) + 1:04d}",
                "term_norm": term_norm,
                "aliases": aliases[:12],
                "category": category,
                "type_code": type_code,
                "zh": entry.get("zh", ""),
                "yue": entry.get("yue", ""),
                "jyutping": entry.get("jyutping", ""),
                "synonyms": entry.get("synonyms", ""),
                "note": entry.get("note", ""),
                "category_zh": entry.get("category", ""),
                "skipped": skipped,
            }
        )
        for alias in aliases:
            candidates.append((prepared[-1], alias))

    # 冲突检测：同一别名指向不同规范写法时整体弃用（避免 血压高 -> Hypertension 与
    # Elevated blood pressure 互相打架）
    owner: dict[str, set[str]] = {}
    for item, alias in candidates:
        owner.setdefault(normalize(alias), set()).add(normalize(item["term_norm"]))

    for item in prepared:
        kept: list[str] = []
        dropped: list[tuple[str, str]] = []
        for alias in item["aliases"]:
            if len(owner.get(normalize(alias), set())) > 1:
                dropped.append((alias, "ambiguous"))
            else:
                kept.append(alias)
        item["aliases"] = kept
        item["skipped"] = [*item["skipped"], *dropped]

        auto = bool(item["term_norm"] and kept)
        glossary.append(
            {
                "term_id": item["id"],
                "term_norm": item["term_norm"],
                "zh": item["zh"],
                "yue": item["yue"],
                "jyutping": item["jyutping"],
                "category_zh": item["category_zh"],
                "type_code": item["type_code"],
                "category": item["category"],
                "aliases": kept,
                "auto_replace": auto,
                "skip_reasons": sorted({reason for _, reason in item["skipped"]}),
                "note": item["note"],
            }
        )
        if auto:
            engine_entries.append(
                {
                    "id": item["id"],
                    "term_norm": item["term_norm"],
                    "aliases": kept,
                    "category": item["category"],
                }
            )

    payload = {
        "version": version,
        "description": (
            "由 医学术语库_v2.xls 按保守策略生成：仅保留长度≥2、非泛词、"
            "非时间/程度/否定/文书类的别名参与自动替换；其余仅作词表查阅。"
            "格式与 MedScribe HK report-engine 的 terms/seed_terms.json 兼容。"
        ),
        "source": "医学术语库_v2.xls",
        "entries": engine_entries,
    }
    return payload, glossary


def write_terms(engine_payload: dict, glossary: list[dict], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "seed_terms.json").write_text(
        json.dumps(engine_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "glossary_full.json").write_text(
        json.dumps(
            {"version": engine_payload["version"], "entries": glossary},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    auto = sum(1 for g in glossary if g["auto_replace"])
    aliases = sum(len(g["aliases"]) for g in glossary)
    return {
        "version": engine_payload["version"],
        "entries_total": len(glossary),
        "entries_auto_replace": auto,
        "alias_total": aliases,
        "skip_reason_counts": _count_reasons(glossary),
    }


def _count_reasons(glossary: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for g in glossary:
        for reason in g.get("skip_reasons", []):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# 纠正算法（与 report-engine v0.1.0 对齐）
# ---------------------------------------------------------------------------
class TermCorrector:
    def __init__(self, entries: Optional[list[dict]] = None, version: str = "") -> None:
        self.version = version
        self.entries = entries or []
        self._alias_map: dict[str, dict] = {}
        for entry in self.entries:
            norm = entry.get("term_norm", "")
            if not norm:
                continue
            for alias in [norm, *entry.get("aliases", [])]:
                key = normalize(alias)
                if key:
                    self._alias_map.setdefault(key, entry)
        self._alias_items = sorted(self._alias_map.items(), key=lambda kv: -len(kv[0]))

    @classmethod
    def load(cls, terms_dir: Path) -> "TermCorrector":
        path = terms_dir / "seed_terms.json"
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(payload.get("entries", []), payload.get("version", ""))

    @staticmethod
    def levenshtein(a: str, b: str, max_dist: int | None = None) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        if len(a) > len(b):
            a, b = b, a
        prev = list(range(len(a) + 1))
        for i, cb in enumerate(b, 1):
            cur = [i]
            for j, ca in enumerate(a, 1):
                cur.append(
                    min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1))
                )
            if max_dist is not None and min(cur) > max_dist:
                return max_dist + 1
            prev = cur
        return prev[-1]

    @staticmethod
    def _find_spans(alias_key: str, hay: str) -> list[tuple[int, int]]:
        """在**折叠后**的文本里定位别名，返回原始下标区间（长度守恒）。"""
        if not hay or not alias_key:
            return []
        if _ASCII_ALIAS.fullmatch(alias_key):
            pat = re.compile(rf"(?<![a-z0-9]){re.escape(alias_key)}(?![a-z0-9])")
            return [m.span() for m in pat.finditer(hay)]
        spans: list[tuple[int, int]] = []
        start = 0
        while True:
            i = hay.find(alias_key, start)
            if i < 0:
                return spans
            spans.append((i, i + len(alias_key)))
            start = i + len(alias_key)

    @staticmethod
    def _folded(text: str) -> str:
        folded = fold(text)
        # 折叠是逐字映射，长度必须一致；万一不一致就退回原串，绝不按下标错位替换
        return folded if len(folded) == len(text) else text.lower()

    def correct_text(self, segment_id: str, text: str) -> tuple[str, list[dict]]:
        corrections: list[dict] = []
        result = text

        for alias_key, entry in self._alias_items:
            if len(alias_key) < 2:
                continue
            norm_term = normalize(entry.get("term_norm", ""))
            spans = self._find_spans(alias_key, self._folded(result))
            if not spans:
                continue
            # 从后往前替换，前面尚未处理的下标仍然有效
            for start, end in reversed(spans):
                before = result[start:end]
                if fold(before) == norm_term:
                    continue
                corrections.append(
                    {
                        "segment_id": segment_id,
                        "before": before,
                        "after": entry["term_norm"],
                        "rule": "exact",
                        "term_id": entry.get("id", ""),
                        "reversible": True,
                    }
                )
                result = result[:start] + entry["term_norm"] + result[end:]

        for token in [t for t in _TOKEN_SPLIT.split(text) if t]:
            if len(token) < _MIN_FUZZY_LEN:
                continue
            token_norm = fold(token)
            if token_norm in self._alias_map:
                continue
            best: tuple[int, dict] | None = None
            limit = _fuzzy_limit(len(token_norm))
            for alias_key, entry in self._alias_items:
                if abs(len(alias_key) - len(token_norm)) > limit:
                    continue
                dist = self.levenshtein(token_norm, alias_key, limit)
                if dist <= limit and (best is None or dist < best[0]):
                    best = (dist, entry)
            if best is None:
                continue
            entry = best[1]
            if normalize(entry.get("term_norm", "")) == token_norm:
                continue
            if token not in result:
                continue
            corrections.append(
                {
                    "segment_id": segment_id,
                    "before": token,
                    "after": entry["term_norm"],
                    "rule": "fuzzy",
                    "term_id": entry.get("id", ""),
                    "reversible": True,
                }
            )
            result = result.replace(token, entry["term_norm"], 1)

        return result, corrections

    def correct_segments(
        self, segments: list[dict], keep_raw: bool = False
    ) -> tuple[list[dict], list[dict]]:
        """keep_raw=True 表示该会话已有纠正记录：保留既有 text_raw 作为撤销基线。

        没有这个开关时，重复执行纠正（例如客户端在连接重置后重发同一次请求）
        会把 text_raw 覆盖成"已经替换过术语"的文本，撤销就退不回原文了。
        """
        out: list[dict] = []
        all_corrections: list[dict] = []
        for seg in segments:
            corrected, corrections = self.correct_text(seg["id"], seg.get("text", ""))
            all_corrections.extend(corrections)
            new_seg = dict(seg)
            if corrected != seg.get("text"):
                new_seg["text"] = corrected
                # text_raw 记录**纠正前**的文本（含人工修正），这样撤销只回退术语替换，
                # 不会把医生的编辑一起丢掉。
                if not (keep_raw and seg.get("text_raw")):
                    new_seg["text_raw"] = seg.get("text", "")
            out.append(new_seg)
        return out, all_corrections
