"""LLM 精修预算回归：调用上限必须按报告重置，而不是按进程累计。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.llm import LLMRefiner  # noqa: E402
from app.schemas import ClinicalElement  # noqa: E402


def _settings() -> Settings:
    s = Settings()
    s.llm_enabled = True
    s.llm_base_url = "http://127.0.0.1:9"
    s.llm_model = "stub"
    s.llm_max_calls_per_report = 1
    return s


def _report(report_id: str):
    return SimpleNamespace(
        id=report_id,
        fields=[SimpleNamespace(key="chief_complaint", ai_value="未提及")],
    )


_ELEMENTS = [
    ClinicalElement(id="E-001", type="S", text="病人主訴頭痛三日", source_segment_ids=["S-001"])
]


class LLMCallBudgetTest(unittest.TestCase):
    def test_budget_resets_for_each_report(self):
        refiner = LLMRefiner(_settings())
        refiner._chat = lambda messages: '{"chief_complaint": "頭痛三日"}'

        accepted, note = refiner.refine(_report("R-1"), _ELEMENTS)
        self.assertEqual(accepted, {"chief_complaint": "頭痛三日"})
        self.assertEqual(refiner.calls, 1)

        accepted, note = refiner.refine(_report("R-1"), _ELEMENTS)
        self.assertEqual(accepted, {})
        self.assertIn("上限", note)

        accepted, note = refiner.refine(_report("R-2"), _ELEMENTS)
        self.assertEqual(accepted, {"chief_complaint": "頭痛三日"})
        self.assertEqual(refiner.calls, 1)

    def test_unavailable_does_not_consume_budget(self):
        refiner = LLMRefiner(_settings())
        refiner.settings.llm_enabled = False
        refiner._chat = lambda messages: '{"chief_complaint": "頭痛"}'
        self.assertEqual(refiner.refine(_report("R-1"), _ELEMENTS), ({}, "LLM 未启用或未配置"))
        self.assertEqual(refiner.calls, 0)


if __name__ == "__main__":
    unittest.main()
