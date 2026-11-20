"""分块解码回归：重叠窗口、切口续句替换与重复文本裁剪。

用桩模型替代 Whisper，只验证分块/去重算法，不加载模型、不依赖网络。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("WHISPER_CHUNK_OVERLAP", "1.0")

from app.services.asr import (  # noqa: E402
    ASRService,
    _extends_previous,
    _trim_overlap_text,
)

_RATE = 16000


class _Info:
    language = "yue"
    language_probability = 0.93


class _Seg:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start, self.end, self.text = start, end, text


class _FakeModel:
    """按调用序号返回预设片段，模拟 ASR 在切口处的截断与重复。"""

    def __init__(self, script: list[list[tuple[float, float, str]]]) -> None:
        self.script = script
        self.calls = 0

    def transcribe(self, chunk, **kwargs):
        index = self.calls
        self.calls += 1
        segments = self.script[index] if index < len(self.script) else []
        return [_Seg(*item) for item in segments], _Info()


def _write_wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(b"\x00\x00" * int(_RATE * seconds))


class OverlapChunkingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wav = Path(self.tmp.name) / "sample.wav"
        _write_wav(self.wav, 130)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _decode(self, script):
        service = ASRService()
        service.model = _FakeModel(script)
        service.current_model_size = "fake"
        service.engine = "whisper"
        return service._decode_all(
            service.model, str(self.wav), "yue", None, 5, lambda *a, **k: None
        )

    def test_truncated_tail_is_replaced_by_full_version(self):
        result = self._decode([
            [(0.0, 10.0, "病人今日返嚟覆診"), (57.0, 60.0, "plane of mesorectal exci")],
            [(0.0, 3.5, "plane of mesorectal excision: complete"), (3.5, 12.0, "no perforation")],
            [(0.0, 5.0, "closing remark")],
        ])
        texts = result["texts"]
        self.assertNotIn("plane of mesorectal exci", texts)
        self.assertIn("plane of mesorectal excision: complete", texts)
        self.assertEqual(texts.count("plane of mesorectal excision: complete"), 1)
        excision = next(s for s in result["segments"] if "excision" in s["text"])
        self.assertAlmostEqual(excision["start"], 57.0, places=2)

    def test_fully_covered_segment_is_dropped(self):
        result = self._decode([
            [(50.0, 59.5, "血壓一百二十八")],
            [(0.0, 0.5, "血壓一百二十八"), (0.5, 10.0, "心跳七十八")],
        ])
        self.assertEqual(result["texts"], ["血壓一百二十八", "心跳七十八"])
        self.assertEqual(result["segments"][1]["start"], 59.5)

    def test_duplicate_prefix_is_trimmed_when_not_extendable(self):
        result = self._decode([
            [(52.0, 59.5, "建議覆診，一星期後")],
            [(0.0, 5.0, "建議覆診，一星期後返嚟")],
        ])
        self.assertEqual(result["texts"], ["建議覆診，一星期後返嚟"])
        self.assertAlmostEqual(result["segments"][0]["start"], 52.0, places=2)

    def test_second_chunk_starts_before_hard_boundary(self):
        result = self._decode([
            [(50.0, 59.8, "first half")],
            [(0.0, 5.0, "second half")],
        ])
        self.assertEqual(result["segments"][1]["start"], 59.0)

    def test_ids_sequence_after_dedup(self):
        result = self._decode([
            [(0.0, 10.0, "a")],
            [(0.0, 1.0, "a"), (1.0, 10.0, "b")],
        ])
        self.assertEqual([s["id"] for s in result["segments"]], [0, 1])


class OverlapHelpersTest(unittest.TestCase):
    def test_trim_removes_duplicated_chinese_prefix(self):
        self.assertEqual(_trim_overlap_text("血壓一百二十八心跳七十八", "血壓一百二十八"), "心跳七十八")

    def test_trim_keeps_unrelated_text(self):
        self.assertEqual(_trim_overlap_text("心跳七十八", "血壓一百二十八"), "心跳七十八")

    def test_trim_strips_leading_punctuation(self):
        self.assertEqual(_trim_overlap_text("，建議覆診", "血壓一百二十八，建議覆診"), "")

    def test_extends_previous_detects_full_version(self):
        self.assertTrue(_extends_previous("plane of mesorectal exci", "plane of mesorectal excision complete"))

    def test_extends_previous_rejects_unrelated(self):
        self.assertFalse(_extends_previous("no perforation", "plane of mesorectal excision"))


if __name__ == "__main__":
    unittest.main()
