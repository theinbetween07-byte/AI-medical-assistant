"""术语纠正（FR-03 / BR-04）。

优先级：术语库精确匹配 > 术语库模糊匹配（编辑距离 ≤ 2，仅词长 ≥ 5）> 原始 ASR 输出。
每次替换生成一条可撤销的 TermCorrection 记录。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .schemas import TermCorrection, TranscriptSegment

_MIN_FUZZY_LEN = 5
_MAX_FUZZY_DIST = 2
_TOKEN_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_ASCII_ALIAS = re.compile(r"[A-Za-z0-9 .\-]+")


def normalize(text: str) -> str:
    """全角转半角、转小写、去首尾空白。"""
    return unicodedata.normalize("NFKC", text).strip().lower()


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


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """纯 ASCII 别名加词边界，避免 'tab' 命中 'tablet'。"""
    escaped = re.escape(alias)
    if _ASCII_ALIAS.fullmatch(alias):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


class Terminology:
    """术语库：词条 + 别名 -> 规范写法。"""

    def __init__(self, entries: list[dict] | None = None, version: str = "") -> None:
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
        # 长别名优先，避免短别名抢先命中
        self._alias_items = sorted(self._alias_map.items(), key=lambda kv: -len(kv[0]))

    @classmethod
    def load(cls, terms_dir: Path) -> "Terminology":
        file_path = terms_dir / "seed_terms.json"
        if not file_path.is_file():
            return cls()
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 术语库损坏时跳过纠正步骤，保留原始转写（技术设计 §5）
            return cls()
        return cls(payload.get("entries", []), payload.get("version", ""))

    def correct_text(self, segment_id: str, text: str) -> tuple[str, list[TermCorrection]]:
        corrections: list[TermCorrection] = []
        result = text

        # 1) 精确匹配（含别名）
        for alias_key, entry in self._alias_items:
            if len(alias_key) < 2:
                continue
            norm_term = normalize(entry.get("term_norm", ""))
            pattern = _alias_pattern(alias_key)
            if not pattern.search(result):
                continue

            def _repl(
                match: re.Match[str], _entry: dict = entry, _norm_term: str = norm_term
            ) -> str:
                if normalize(match.group(0)) == _norm_term:
                    return match.group(0)
                corrections.append(
                    TermCorrection(
                        segment_id=segment_id,
                        before=match.group(0),
                        after=_entry["term_norm"],
                        rule="exact",
                        term_id=_entry.get("id", ""),
                    )
                )
                return _entry["term_norm"]

            result = pattern.sub(_repl, result)

        # 2) 模糊匹配：token 级编辑距离，仅长词启用
        for token in [t for t in _TOKEN_SPLIT.split(text) if t]:
            if len(token) < _MIN_FUZZY_LEN:
                continue
            token_norm = normalize(token)
            if token_norm in self._alias_map:
                continue
            best: tuple[int, dict] | None = None
            for alias_key, entry in self._alias_items:
                if abs(len(alias_key) - len(token_norm)) > _MAX_FUZZY_DIST:
                    continue
                dist = levenshtein(token_norm, alias_key, _MAX_FUZZY_DIST)
                if dist <= _MAX_FUZZY_DIST and (best is None or dist < best[0]):
                    best = (dist, entry)
            if best is None:
                continue
            entry = best[1]
            if normalize(entry.get("term_norm", "")) == token_norm:
                continue
            pattern = re.compile(re.escape(token))
            if not pattern.search(result):
                continue

            def _fuzzy_repl(match: re.Match[str], _entry: dict = entry) -> str:
                corrections.append(
                    TermCorrection(
                        segment_id=segment_id,
                        before=match.group(0),
                        after=_entry["term_norm"],
                        rule="fuzzy",
                        term_id=_entry.get("id", ""),
                    )
                )
                return _entry["term_norm"]

            result = pattern.sub(_fuzzy_repl, result, count=1)

        return result, corrections

    def correct_segments(
        self, segments: list[TranscriptSegment]
    ) -> tuple[list[TranscriptSegment], list[TermCorrection]]:
        out: list[TranscriptSegment] = []
        all_corrections: list[TermCorrection] = []
        for seg in segments:
            corrected, corrections = self.correct_text(seg.id, seg.text)
            all_corrections.extend(corrections)
            out.append(
                seg.model_copy(
                    update={
                        "text": corrected,
                        "text_raw": seg.text_raw or seg.text,
                    }
                )
            )
        return out, all_corrections
