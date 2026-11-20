"""运行时规则副本与引擎默认规则必须一致。

`start_all.py` 把 `integration/terms` 作为 `MSHK_TERMS_DIR` 传给引擎，
所以打包后实际生效的是这一份副本；两份分叉会让改在 `report-engine/terms`
里的修复在生产静默失效（v1.0.3 的 marker_bonus 就踩过这个坑）。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

INTEGRATION_RULES = Path(__file__).resolve().parents[1] / "terms" / "extraction_rules.json"
ENGINE_RULES = (
    Path(__file__).resolve().parents[2] / "report-engine" / "terms" / "extraction_rules.json"
)


class RulesSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        if not ENGINE_RULES.is_file():
            self.skipTest("未找到 report-engine 规则文件，跳过跨目录比对")

    def test_integration_rules_match_engine_rules(self) -> None:
        self.assertEqual(
            json.loads(INTEGRATION_RULES.read_text(encoding="utf-8")),
            json.loads(ENGINE_RULES.read_text(encoding="utf-8")),
            "integration/terms 与 report-engine/terms 的抽取规则已分叉",
        )

    def test_marker_bonus_is_effective(self) -> None:
        rules = json.loads(INTEGRATION_RULES.read_text(encoding="utf-8"))
        thresholds = rules.get("thresholds", {})
        self.assertIn("marker_bonus", thresholds)
        self.assertGreater(float(thresholds["marker_bonus"]), 0.0)


if __name__ == "__main__":
    unittest.main()
