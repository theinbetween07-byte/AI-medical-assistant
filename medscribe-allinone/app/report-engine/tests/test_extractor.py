"""抽取层回归：ASCII 词边界与标记词加权配置。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.extractor import ExtractionRules, Sentence, classify  # noqa: E402


def _classify(text: str, rules: ExtractionRules | None = None):
    rules = rules or ExtractionRules.load(ROOT / "terms")
    return classify(Sentence(text=text, segment_id="S-001", start_ms=0, end_ms=1000), rules)


class WordBoundaryTest(unittest.TestCase):
    """v1.0.3：ASCII 词表项必须按词边界匹配，避免子串误命中。"""

    def setUp(self) -> None:
        self.rules = ExtractionRules.load(ROOT / "terms")

    def test_general_practice_does_not_hit_ct(self):
        section, score, matched = _classify(
            "patient attends general practice for review", self.rules
        )
        self.assertEqual(section, "uncategorized")
        self.assertEqual(score, 0.0)
        self.assertEqual(matched, [])

    def test_positive_does_not_hit_po(self):
        section, _, matched = _classify("patient is positive for hepatitis B", self.rules)
        self.assertNotEqual(section, "P")
        self.assertFalse([m for m in matched if m == "kw:po"], matched)

    def test_labour_does_not_hit_lab(self):
        _, _, matched = _classify("labour pain since last night", self.rules)
        self.assertNotIn("marker:lab", matched)

    def test_abdominal_does_not_hit_bd(self):
        _, _, matched = _classify("abdominal examination revealed tenderness", self.rules)
        self.assertNotIn("kw:bd", matched)

    def test_ascii_terms_still_match_with_boundary(self):
        section, _, matched = _classify("CT scan of thorax", self.rules)
        self.assertEqual(section, "O")
        self.assertIn("marker:ct", matched)

    def test_unit_after_digit_still_matches(self):
        _, _, matched = _classify("amlodipine 5mg daily", self.rules)
        self.assertIn("kw:mg", matched)

    def test_chinese_keywords_keep_substring_matching(self):
        section, _, matched = _classify("血壓一百二十八", self.rules)
        self.assertEqual(section, "O")
        self.assertIn("kw:血壓", matched)

    def test_multiword_phrase_tolerates_hyphen(self):
        _, _, matched = _classify("follow-up in 2 weeks", self.rules)
        self.assertIn("kw:follow up", matched)


class MarkerBonusConfigTest(unittest.TestCase):
    """v1.0.3：配置键统一为 marker_bonus，同时兼容老键 marker_score。"""

    def test_current_rules_expose_bonus(self):
        rules = ExtractionRules.load(ROOT / "terms")
        self.assertAlmostEqual(rules.marker_bonus, 1.6)

    def test_marker_bonus_key_wins(self):
        rules = ExtractionRules.from_dict({"thresholds": {"marker_bonus": 2.2, "marker_score": 0.0}})
        self.assertAlmostEqual(rules.marker_bonus, 2.2)

    def test_legacy_marker_score_key_accepted(self):
        rules = ExtractionRules.from_dict({"thresholds": {"marker_score": 0.8}})
        self.assertAlmostEqual(rules.marker_bonus, 0.8)

    def test_invalid_value_falls_back_to_default(self):
        rules = ExtractionRules.from_dict({"thresholds": {"marker_bonus": "oops"}})
        self.assertAlmostEqual(rules.marker_bonus, 1.6)


class NarrativePlanGuardTest(unittest.TestCase):
    """v1.0.3：「覆診」既是计划词也是叙述词，靠时间指向区分。"""

    def setUp(self) -> None:
        self.rules = ExtractionRules.load(ROOT / "terms")

    def test_today_visit_is_narrative_not_plan(self):
        section, _, matched = _classify("病人今日返嚟覆診。", self.rules)
        self.assertEqual(section, "S")
        self.assertIn("guard:narrative_past", matched)

    def test_future_review_stays_plan(self):
        section, _, _ = _classify("建議一星期後覆診。", self.rules)
        self.assertEqual(section, "P")

    def test_plan_with_arrangement_words_stays_plan(self):
        section, _, _ = _classify("安排今日入院。", self.rules)
        self.assertEqual(section, "P")


if __name__ == "__main__":
    unittest.main()
