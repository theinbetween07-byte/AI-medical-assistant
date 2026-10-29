"""临床要素抽取（FR-04 / BR-09）——离线规则内核。

策略：
  1. 把转写分段按标点切成句子，按字符长度比例分配时间戳（NFR-12 可追溯）；
  2. 章节标记词（主诉/檢查/診斷/計劃…）给出强信号，关键词表给出弱信号，加权求和；
  3. 相邻同类别句子合并为一个要素，保留全部来源片段；
  4. 得分不足阈值的句子进入 uncategorized，不丢弃（AC-04）；
  5. 来源含低置信度片段的要素被标记，进入报告后提示医生核对（BR-09）。

规则来自 terms/extraction_rules.json，可热改而无需改代码。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .schemas import ClinicalElement, ElementType, SourceSpan, TranscriptSegment

SENTENCE_RE = re.compile(
    # 句末标点（。！？；．）或 ASCII [.!?] 后接空白/行尾 —— 后者避免把 "36.8 degree" 切断
    r"[^。！？；．\n]+?(?:[。！？；．]+|(?<=[\w\u4e00-\u9fff\)\]])[.!?](?=\s|$)|\n|$)",
    re.UNICODE,
)
LOW_CONFIDENCE_THRESHOLD = 0.6
SECTIONS: tuple[ElementType, ...] = ("S", "O", "A", "P")

_DEFAULT_RULES: dict = {
    "markers": {"S": ["主訴", "主诉", "history"], "O": ["檢查", "检查", "examination"],
                "A": ["診斷", "诊断", "diagnosis"], "P": ["計劃", "计划", "plan"]},
    "keywords": {"S": {"痛": 1.0, "pain": 1.0}, "O": {"血壓": 1.2, "BP": 1.0},
                 "A": {"diagnosis": 1.5}, "P": {"mg": 1.5, "follow up": 1.2}},
    "thresholds": {"assign_min_score": 1.0},
}


@dataclass
class Sentence:
    text: str
    segment_id: str
    start_ms: int
    end_ms: int
    lang: str = "unknown"
    confidence: float = 1.0
    low_confidence: bool = False


@dataclass
class ExtractionRules:
    markers: dict[str, list[str]] = field(default_factory=dict)
    keywords: dict[str, dict[str, float]] = field(default_factory=dict)
    assign_min_score: float = 1.0
    marker_bonus: float = 1.6
    version: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionRules":
        thresholds = data.get("thresholds", {})
        markers = {
            k: sorted({m.lower() for m in v if m}, key=len, reverse=True)
            for k, v in data.get("markers", {}).items()
        }
        keywords = {
            k: {kw.lower(): float(weight) for kw, weight in v.items()}
            for k, v in data.get("keywords", {}).items()
        }
        return cls(
            markers=markers,
            keywords=keywords,
            assign_min_score=float(thresholds.get("assign_min_score", 1.0)),
            marker_bonus=float(thresholds.get("marker_bonus", 1.6)),
            version=data.get("version", ""),
        )

    @classmethod
    def load(cls, terms_dir: Path) -> "ExtractionRules":
        file_path = terms_dir / "extraction_rules.json"
        if file_path.is_file():
            try:
                return cls.from_dict(json.loads(file_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        return cls.from_dict(_DEFAULT_RULES)


def split_sentences(segment: TranscriptSegment) -> list[Sentence]:
    """切句并按字符占比分配时间，保证每个要素可跳转到音频时间点。"""
    text = segment.text or ""
    spans = [(m.group(0).strip(), m.start(), m.end()) for m in SENTENCE_RE.finditer(text)]
    spans = [s for s in spans if s[0]]
    if not spans:
        return []

    total_chars = max(1, len(text))
    duration = max(0, segment.end_ms - segment.start_ms)
    out: list[Sentence] = []
    for chunk, off_start, _off_end in spans:
        start_ms = segment.start_ms + int(duration * (off_start / total_chars))
        end_ms = segment.start_ms + int(duration * ((off_start + len(chunk)) / total_chars))
        out.append(
            Sentence(
                text=chunk,
                segment_id=segment.id,
                start_ms=start_ms,
                end_ms=max(start_ms, end_ms),
                lang=segment.lang,
                confidence=segment.confidence,
                low_confidence=segment.low_confidence
                or segment.confidence < LOW_CONFIDENCE_THRESHOLD,
            )
        )
    # 补齐最后一个句子的结束时间为整段结束
    if out:
        out[-1].end_ms = max(out[-1].end_ms, segment.end_ms)
    return out


def classify(sentence: Sentence, rules: ExtractionRules) -> tuple[ElementType, float, list[str]]:
    """返回 (类别, 得分, 命中规则列表)。"""
    haystack = sentence.text.lower()
    scores: dict[str, float] = {s: 0.0 for s in SECTIONS}
    matched: list[str] = []

    # 章节标记词：最长命中优先，避免 "檢查" 与 "報告" 同时命中时的歧义
    for section, markers in rules.markers.items():
        for marker in markers:
            if marker and marker in haystack:
                scores[section] = scores.get(section, 0.0) + rules.marker_bonus
                matched.append(f"marker:{marker}")
                break

    # 关键词打分
    for section, table in rules.keywords.items():
        for keyword, weight in table.items():
            if keyword and keyword in haystack:
                scores[section] = scores.get(section, 0.0) + weight
                matched.append(f"kw:{keyword}")

    if not matched:
        return "uncategorized", 0.0, []
    best = max(SECTIONS, key=lambda s: scores.get(s, 0.0))
    if scores.get(best, 0.0) < rules.assign_min_score:
        return "uncategorized", scores.get(best, 0.0), matched
    return best, scores.get(best, 0.0), matched


def extract_elements(
    segments: list[TranscriptSegment],
    rules: ExtractionRules,
    keep_unclassified: bool = True,
) -> list[ClinicalElement]:
    """逐句抽取：一个句子 = 一个要素。

    以句子为最小粒度（而非合并相邻同类句子），是让字段选材可以按关键词精确匹配，
    避免"过敏史"与"既往史"共用一段口述时互相抢占内容。
    """
    elements: list[ClinicalElement] = []

    for segment in segments:
        for sentence in split_sentences(segment):
            section, score, matched = classify(sentence, rules)
            if section == "uncategorized" and not keep_unclassified:
                continue
            text = sentence.text.strip()
            if not text:
                continue
            tags = (
                [f"low_confidence:{sentence.segment_id}"] if sentence.low_confidence else []
            )
            elements.append(
                ClinicalElement(
                    id=f"E-{len(elements) + 1:03d}",
                    type=section,
                    text=text,
                    source_segment_ids=[sentence.segment_id],
                    sources=[
                        SourceSpan(
                            segment_id=sentence.segment_id,
                            start_ms=sentence.start_ms,
                            end_ms=sentence.end_ms,
                            text=text,
                        )
                    ],
                    score=round(score, 2),
                    matched_rules=sorted(set(matched)) + tags,
                )
            )
    return elements


def unclassified_count(elements: list[ClinicalElement]) -> int:
    return sum(1 for e in elements if e.type == "uncategorized")
