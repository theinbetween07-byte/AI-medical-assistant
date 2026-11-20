"""字段归属回归（P1）：标题作用域、同分区续写、引语剥离与残句兜底。

这些用例全部来自 v1.0.2 的实测错位：
  - 病理「鏡下檢查」整段被 "invading through / lymph nodes / margins" 抢走；
  - SOAP 查体续述 "Throat is mildly injected" 无人认领；
  - 主诉字段存成「病人講喉嚨痛…」；
  - 现病史整段为空。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.generator import assign_elements, build_fields  # noqa: E402
from app.schemas import ClinicalElement, SourceSpan  # noqa: E402
from app.templates import TemplateRegistry  # noqa: E402


def _el(index: int, kind: str, text: str, at_ms: int) -> ClinicalElement:
    return ClinicalElement(
        id=f"E-{index:03d}",
        type=kind,  # type: ignore[arg-type]
        text=text,
        source_segment_ids=[f"SEG-{index:03d}"],
        sources=[
            SourceSpan(
                segment_id=f"SEG-{index:03d}",
                start_ms=at_ms,
                end_ms=at_ms + 3000,
                text=text,
            )
        ],
    )


class PathologyScopeTest(unittest.TestCase):
    """标题建立的强作用域不能被段落内的关键词抢占。"""

    def setUp(self) -> None:
        self.template = TemplateRegistry(ROOT / "templates").get("pathology_colorectal")
        assert self.template is not None

    def _fields(self, texts: list[str]) -> dict[str, str]:
        elements = [_el(i + 1, "uncategorized", t, i * 3000) for i, t in enumerate(texts)]
        fields, _, _ = build_fields(self.template, elements)
        return {f.key: f.ai_value for f in fields}

    def test_microscopic_narrative_stays_in_microscopic(self):
        values = self._fields(
            [
                "Extent of invasion: tumour invades through muscularis propria into subserosa.",
                "Lymph node status: 2 out of 16 lymph nodes show metastatic carcinoma.",
                "Proximal margin: free of tumour, 6.0 cm from tumour.",
                "Macroscopic examination.",
                "The specimen consists of a segment of sigmoid colon measuring 18.0 cm in length.",
                "Microscopic examination.",
                "Sections show a moderately differentiated adenocarcinoma, invading through the muscularis propria.",
                "Metastatic carcinoma is present in 2 of 16 lymph nodes.",
                "Sections of the proximal, distal and circumferential resection margins are free of tumour.",
            ]
        )
        # 镜下叙述不再被 synoptic 字段抢走
        self.assertIn("Sections show a moderately differentiated", values["microscopic_examination"])
        self.assertIn("Metastatic carcinoma is present", values["microscopic_examination"])
        self.assertIn("resection margins are free of tumour", values["microscopic_examination"])
        # synoptic 字段保持单一值，不再混入叙述
        self.assertEqual(
            values["extent_of_invasion"],
            "tumour invades through muscularis propria into subserosa",
        )
        self.assertEqual(
            values["lymph_node_status"],
            "2 out of 16 lymph nodes show metastatic carcinoma",
        )
        self.assertEqual(values["proximal_margin"], "free of tumour, 6.0 cm from tumour")
        # 宏观段落收下宏观叙述
        self.assertIn("The specimen consists", values["macroscopic_examination"])

    def test_macroscopic_sentence_mentioning_margin_is_not_margin(self):
        values = self._fields(
            [
                "Proximal margin: free of tumour, 6.0 cm from tumour.",
                "Macroscopic examination.",
                "An ulcerated tumour measuring 3.5 x 2.8 x 1.2 cm is identified 6.0 cm from the proximal resection margin.",
            ]
        )
        self.assertEqual(values["proximal_margin"], "free of tumour, 6.0 cm from tumour")
        self.assertIn("ulcerated tumour", values["macroscopic_examination"])

    def test_new_heading_switches_scope(self):
        values = self._fields(
            [
                "Macroscopic examination.",
                "The specimen measures 18 cm.",
                "Microscopic examination.",
                "Sections show adenocarcinoma.",
            ]
        )
        self.assertEqual(values["macroscopic_examination"], "The specimen measures 18 cm.")
        self.assertEqual(values["microscopic_examination"], "Sections show adenocarcinoma.")


class SoapAssignmentTest(unittest.TestCase):
    """SOAP：引语剥离、同分区续写、现病史兜底。"""

    def setUp(self) -> None:
        self.template = TemplateRegistry(ROOT / "templates").get("general_soap")
        assert self.template is not None

    def _build(self, elements: list[ClinicalElement]) -> dict[str, str]:
        fields, unclaimed, _ = build_fields(self.template, elements)
        self.unclaimed = unclaimed
        return {f.key: f.ai_value for f in fields}

    def test_speech_cue_is_stripped(self):
        values = self._build(
            [
                _el(1, "S", "病人講喉嚨痛，痛咗三日。", 0),
                _el(2, "S", "complains of chest pain for two days.", 3000),
            ]
        )
        self.assertEqual(values["chief_complaint"], "喉嚨痛，痛咗三日；chest pain for two days")

    def test_examination_continuation_joins_previous_field(self):
        values = self._build(
            [
                _el(1, "O", "On examination, blood pressure 128 over 76.", 0),
                _el(2, "O", "Throat is mildly injected, chest clear.", 3000),
            ]
        )
        self.assertIn("blood pressure", values["physical_examination"])
        self.assertIn("Throat is mildly injected", values["physical_examination"])
        self.assertEqual(self.unclaimed, [])

    def test_unmatched_s_sentence_falls_back_to_hpi(self):
        values = self._build(
            [
                _el(1, "S", "陳先生今日返嚟覆診，佢話呢三日有喉嚨痛同咳，無發燒。", 0),
            ]
        )
        self.assertIn("陳先生", values["history_of_present_illness"])

    def test_specific_keywords_still_win_inside_section(self):
        values = self._build(
            [
                _el(1, "O", "On examination, blood pressure 128 over 76.", 0),
                _el(2, "O", "Blood test shows Hb 11.2.", 3000),
            ]
        )
        self.assertIn("Blood test", values["investigations"])
        self.assertNotIn("Blood test", values["physical_examination"])


class AssignLeavesUncategorizedAloneTest(unittest.TestCase):
    def test_uncategorized_chatter_does_not_enter_any_field(self):
        template = TemplateRegistry(ROOT / "templates").get("general_soap")
        assert template is not None
        elements = [_el(1, "uncategorized", "random chatter", 0)]
        selection, unclaimed, _ = assign_elements(template, elements)
        self.assertTrue(all(not items for items in selection.values()))
        # 无上下文的口语残句不塞进字段，但必须作为未覆盖内容上报（AC-04）
        self.assertEqual([e.id for e in unclaimed], ["E-001"])

    def test_uncategorized_continuation_follows_previous_field(self):
        template = TemplateRegistry(ROOT / "templates").get("general_soap")
        assert template is not None
        elements = [
            _el(1, "O", "On examination, blood pressure 128 over 76.", 0),
            _el(2, "uncategorized", "Throat is erythematous.", 3000),
        ]
        selection, unclaimed, _ = assign_elements(template, elements)
        self.assertEqual([e.id for e in selection["physical_examination"]], ["E-001", "E-002"])
        self.assertEqual(unclaimed, [])


if __name__ == "__main__":
    unittest.main()
