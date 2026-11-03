"""集成层测试（只用标准库：unittest + urllib + http.server）。

    python -m unittest tests.test_integration -v

三层覆盖：
    TermStrategyTest   术语库的保守导入策略与纠正算法（不需要网络）
    AudioRuleTest      BR-01/BR-02 时长与静音规则（不需要网络）
    EndToEndTest       真实 HTTP：上传 → 转写 → 纠正 → 抽取 → 生成 → 校审 → 签署 → 导出
                       上游 ASR 与报告引擎用进程内 stub 替代，契约与真实服务一致
"""

from __future__ import annotations

import io
import importlib.util
import json
import math
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audio import validate as validate_audio  # noqa: E402
from app.config import Settings  # noqa: E402
from app.terms import TermCorrector, compile_terms  # noqa: E402
from app.postprocess import EMPTY_MARK, hide_rebalanced_source, rebalance_report  # noqa: E402
from app.suggest import assessment_hints, chief_candidate, suggest_missing  # noqa: E402
from app.textsplit import refine_segments  # noqa: E402


# ===========================================================================
# 工具
# ===========================================================================
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_wav(seconds: float = 12.0, freq: int = 440, amp: int = 9000) -> bytes:
    buf = io.BytesIO()
    rate = 16000
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            value = int(amp * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", value)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def silence_wav(seconds: float = 12.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(handler_cls) -> tuple[_Server, str]:
    port = free_port()
    srv = _Server(("127.0.0.1", port), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}


def send_json(handler, code: int, payload) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ===========================================================================
# 上游 stub：ASR
# ===========================================================================
class AsrState:
    probability = 0.92
    segments = [
        {"id": 0, "start": 0.0, "end": 4.0, "text": "病人今日有發燒，血壓高"},
        {"id": 1, "start": 4.0, "end": 8.0, "text": "照咗胸肺片，無明顯異常"},
        {"id": 2, "start": 8.0, "end": 12.0, "text": "建議食退燒藥，兩日後覆診"},
    ]
    registered = False
    token = "stub-asr-token"
    calls = {"transcribe": 0}


class AsrHandler(BaseHTTPRequestHandler):
    # HTTP/1.1 keep-alive：避免 Windows 下"响应后立即关闭"触发的间歇性 WinError 10054
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静默
        pass

    def do_GET(self):
        if self.path == "/health":
            return send_json(self, 200, {"status": "ok", "model": "stub-whisper"})
        if self.path == "/asr/models":
            return send_json(self, 200, {"models": ["stub-whisper"]})
        return send_json(self, 404, {"detail": "not found"})

    def do_POST(self):
        if self.path == "/auth/register":
            body = read_json(self)
            AsrState.registered = True
            return send_json(self, 200, {"id": 1, "username": body.get("username")})
        if self.path == "/auth/login":
            read_json(self)  # 读掉 body，避免 keep-alive 下残留数据
            if not AsrState.registered:
                return send_json(self, 401, {"detail": "用户不存在"})
            return send_json(self, 200, {"token": AsrState.token,
                                         "user": {"username": "demo_doctor"}})
        if self.path == "/asr/transcribe":
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {AsrState.token}":
                return send_json(self, 401, {"detail": "未认证"})
            AsrState.calls["transcribe"] += 1
            # 读掉 body，避免连接复用问题
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            return send_json(self, 200, {
                "language": "yue",
                "language_probability": AsrState.probability,
                "duration": 12.0,
                "text": " ".join(s["text"] for s in AsrState.segments),
                "segments": AsrState.segments,
                "elapsed": 0.42,
                "template": "soap",
                "model": "stub-whisper",
            })
        return send_json(self, 404, {"detail": "not found"})


# ===========================================================================
# 上游 stub：报告引擎（契约对齐 report-engine 0.1.0）
# ===========================================================================
class EngineState:
    reports: dict[str, dict] = {}
    exports_dir = Path(tempfile.gettempdir()) / "mshk-test-exports"
    calls = {"generate": 0, "reload": 0}
    last_segments: list[str] = []


SECTION_FIELD = {"S": "SUBJECTIVE", "O": "OBJECTIVE", "A": "ASSESSMENT", "P": "PLAN"}


def build_report(report_id: str, session_id: str, template_id: str, elements: list[dict]) -> dict:
    fields = []
    for sec, key in SECTION_FIELD.items():
        texts = [e["text"] for e in elements if e.get("type") == sec]
        fields.append({
            "key": key,
            "label": {"S": "主观", "O": "客观", "A": "评估", "P": "计划"}[sec],
            "section": sec,
            "required": key in ("SUBJECTIVE", "ASSESSMENT"),
            "order": list(SECTION_FIELD).index(sec),
            "type": "text",
            "ai_value": "；".join(texts),
            "final_value": "",
            "sources": [{"segment_id": s, "start_ms": 0, "end_ms": 0, "text": ""}
                        for e in elements if e.get("type") == sec
                        for s in e.get("source_segment_ids", [])],
            "flags": [],
            "evidence_ok": bool(texts),
            "note": "" if texts else "未提及",
        })
    missing = [f["key"] for f in fields if f["required"] and not f["ai_value"].strip()]
    return {
        "id": report_id,
        "session_id": session_id,
        "template_id": template_id,
        "template_name": "通用 SOAP",
        "template_version": "1.0.0",
        "status": "draft",
        "created_at": "2026-10-29T00:00:00+00:00",
        "signed_at": None,
        "signed_by": None,
        "fields": fields,
        "missing_required": missing,
        "warnings": [],
        "mode": "rules",
        "reviews": [],
    }


class EngineHandler(BaseHTTPRequestHandler):
    # HTTP/1.1 keep-alive：避免 Windows 下"响应后立即关闭"触发的间歇性 WinError 10054
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def do_GET(self):
        path = self._path()
        if path == "/health":
            return send_json(self, 200, {"ok": True, "templates": 1,
                                         "terms": {"version": "test", "entries": 1}})
        if path == "/api/templates":
            return send_json(self, 200, [{"id": "general_soap", "name": "通用 SOAP",
                                          "specialty": "general"}])
        m = re.match(r"^/api/reports/([^/]+)$", path)
        if m:
            rid = unquote(m.group(1))
            if rid not in EngineState.reports:
                return send_json(self, 404, {"detail": "报告不存在"})
            return send_json(self, 200, EngineState.reports[rid])
        m = re.match(r"^/api/reports/([^/]+)/preview$", path)
        if m:
            rid = unquote(m.group(1))
            report = EngineState.reports.get(rid)
            if not report:
                return send_json(self, 404, {"detail": "报告不存在"})
            body = "\n".join(f"{f['label']}: {f['final_value'] or f['ai_value']}"
                             for f in report["fields"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        if path == "/api/audit":
            return send_json(self, 200, [])
        return send_json(self, 404, {"detail": "not found"})

    def do_DELETE(self):
        m = re.match(r"^/api/reports/([^/]+)$", self._path())
        if not m:
            return send_json(self, 404, {"detail": "not found"})
        removed = EngineState.reports.pop(unquote(m.group(1)), None) is not None
        return send_json(self, 200, {"deleted": removed})

    def do_POST(self):
        path = self._path()
        if path == "/api/reload":
            EngineState.calls["reload"] += 1
            return send_json(self, 200, {"reloaded": True})
        if path == "/api/generate":
            body = read_json(self)
            EngineState.calls["generate"] += 1
            segments = body.get("segments") or []
            EngineState.last_segments = [s.get("text", "") for s in segments]
            elements = body.get("elements")
            if not elements:
                elements = [
                    {
                        "id": f"el-{i+1}",
                        "type": "SOAP"[i % 4],
                        "text": seg["text"],
                        "source_segment_ids": [seg["id"]],
                        "sources": [{"segment_id": seg["id"], "start_ms": seg.get("start_ms", 0),
                                     "end_ms": seg.get("end_ms", 0), "text": seg["text"]}],
                        "state": "pending",
                        "score": 0.9,
                        "matched_rules": ["stub"],
                    }
                    for i, seg in enumerate(segments)
                ]
            rid = f"rep_{int(time.time()*1000)}_{len(EngineState.reports)}"
            report = build_report(rid, body.get("session_id", ""),
                                  body.get("template_id", "general_soap"), elements)
            EngineState.reports[rid] = report
            return send_json(self, 200, {
                "session_id": body.get("session_id", ""),
                "report": report,
                "elements": elements,
                "corrections": [],
                "deidentified": bool(body.get("options", {}).get("deidentify")),
                "audit": [],
                "pipeline": ["stub-extract", "stub-fill"],
            })
        m = re.match(r"^/api/reports/([^/]+)/review/(.+)$", path)
        if m:
            rid, key = unquote(m.group(1)), unquote(m.group(2))
            report = EngineState.reports.get(rid)
            if not report:
                return send_json(self, 404, {"detail": "报告不存在"})
            body = read_json(self)
            action = body.get("action", "accept")
            for field in report["fields"]:
                if field["key"] != key:
                    continue
                if action == "edit":
                    field["final_value"] = body.get("value") or ""
                elif action == "accept":
                    field["final_value"] = field["ai_value"]
                elif action == "reject":
                    field["final_value"] = ""
                report["reviews"] = [r for r in report["reviews"] if r["field_key"] != key] + [{
                    "report_id": rid, "field_key": key, "ai_value": field["ai_value"],
                    "final_value": field["final_value"], "action": action,
                    "reviewed_at": "2026-10-29T00:00:01+00:00",
                    "reviewer": body.get("reviewer", "demo-doctor"),
                }]
            report["missing_required"] = [
                f["key"] for f in report["fields"]
                if f["required"] and not (f["final_value"] or f["ai_value"]).strip()
            ]
            return send_json(self, 200, report)
        m = re.match(r"^/api/reports/([^/]+)/sign$", path)
        if m:
            rid = unquote(m.group(1))
            report = EngineState.reports.get(rid)
            if not report:
                return send_json(self, 404, {"detail": "报告不存在"})
            body = read_json(self)
            missing = [f["key"] for f in report["fields"]
                       if f["required"] and not (f["final_value"] or f["ai_value"]).strip()
                       and not any(r["field_key"] == f["key"] and r["action"] == "reject"
                                   for r in report["reviews"])]
            if missing:
                return send_json(self, 200, {"ok": False, "report": None,
                                             "blocked_by": missing,
                                             "message": "必填字段未完成"})
            report["status"] = "signed"
            report["signed_by"] = body.get("signed_by", "demo-doctor")
            report["signed_at"] = "2026-10-29T00:00:02+00:00"
            return send_json(self, 200, {"ok": True, "report": report, "blocked_by": [],
                                         "message": "已签署"})
        m = re.match(r"^/api/reports/([^/]+)/export$", path)
        if m:
            rid = unquote(m.group(1))
            report = EngineState.reports.get(rid)
            if not report:
                return send_json(self, 404, {"detail": "报告不存在"})
            body = read_json(self)
            EngineState.exports_dir.mkdir(parents=True, exist_ok=True)
            records = []
            for fmt in body.get("formats", []):
                target = EngineState.exports_dir / f"{rid}.{ 'txt' if fmt == 'text' else fmt}"
                target.write_text(f"report {rid} as {fmt}", encoding="utf-8")
                records.append({"report_id": rid, "format": fmt, "path": str(target),
                                "exported_at": "2026-10-29T00:00:03+00:00"})
            return send_json(self, 200, records)
        return send_json(self, 404, {"detail": "not found"})


# ===========================================================================
# 1. 术语策略
# ===========================================================================
class TermStrategyTest(unittest.TestCase):
    ROWS = [
        {"term_id": "dis-001", "category": "疾病", "zh": "高血压", "en": "Hypertension",
         "yue": "血壓高／血压高", "jyutping": "", "type_code": "DIS", "synonyms": "高血壓",
         "note": ""},
        {"term_id": "tme-001", "category": "时间", "zh": "今日", "en": "Today",
         "yue": "今日", "jyutping": "", "type_code": "TME", "synonyms": "", "note": ""},
        {"term_id": "sym-001", "category": "症状", "zh": "病人", "en": "Patient",
         "yue": "病人", "jyutping": "", "type_code": "SYM", "synonyms": "", "note": ""},
        {"term_id": "dis-002", "category": "疾病", "zh": "血压高", "en": "Elevated blood pressure",
         "yue": "血压高", "jyutping": "", "type_code": "DIS", "synonyms": "", "note": ""},
        {"term_id": "lab-001", "category": "化验", "zh": "全血细胞计数", "en": "Complete blood count",
         "yue": "", "jyutping": "", "type_code": "LAB", "synonyms": "CBC", "note": ""},
    ]

    def test_conservative_filter(self):
        payload, glossary = compile_terms(self.ROWS, "test-1")
        idx = {g["term_id"]: g for g in glossary}

        # 时间类不参与自动替换
        self.assertFalse(idx["tme-001"]["auto_replace"])
        self.assertIn("type_not_auto:TME", idx["tme-001"]["skip_reasons"])

        # 泛词「病人」被停用词表挡掉
        self.assertFalse(idx["sym-001"]["auto_replace"])
        self.assertIn("stoplist", idx["sym-001"]["skip_reasons"])

        # 「血压高」同时属于 dis-001 与 dis-002 的别名 -> 整体弃用，避免互相打架
        self.assertIn("ambiguous", idx["dis-001"]["skip_reasons"])
        self.assertIn("ambiguous", idx["dis-002"]["skip_reasons"])
        self.assertNotIn("血压高", idx["dis-001"]["aliases"])

        # 高血压保留：中文标准名 + 其他别名
        self.assertTrue(idx["dis-001"]["auto_replace"])
        self.assertIn("高血压", idx["dis-001"]["aliases"])

        # 引擎词表只包含 auto_replace 的条目
        ids = {e["id"] for e in payload["entries"]}
        self.assertIn("dis-001", ids)
        self.assertNotIn("tme-001", ids)
        for entry in payload["entries"]:
            self.assertIn(entry["category"], {"drug", "disease", "exam", "abbr"})
            self.assertTrue(entry["aliases"])

    def test_corrector_exact_and_fuzzy(self):
        payload, _ = compile_terms(self.ROWS, "test-1")
        corrector = TermCorrector(payload["entries"], payload["version"])

        # 繁体输入经折叠后命中简体别名（粤语 ASR 的常态输入）
        text = "病人有高血壓，今日覆診，做咗 CBC"
        fixed, corrections = corrector.correct_text("S-001", text)
        self.assertIn("Hypertension", fixed)
        self.assertNotIn("高血壓", fixed)
        self.assertTrue(all(c["reversible"] for c in corrections))
        self.assertTrue(all(c["segment_id"] == "S-001" for c in corrections))

        # 模糊匹配：全血细胞记数 与 全血细胞计数（编辑距离 1，词长 6 ≥ 5）
        fuzzy, _ = corrector.correct_text("S-002", "做咗 全血细胞记数")
        self.assertIn("Complete blood count", fuzzy)

        # 已正确的规范写法不应产生替换记录
        same, recs = corrector.correct_text("S-003", "Hypertension")
        self.assertEqual(same, "Hypertension")
        self.assertEqual(recs, [])

    def test_correct_segments_marks_raw(self):
        payload, _ = compile_terms(self.ROWS, "test-1")
        corrector = TermCorrector(payload["entries"], payload["version"])
        segs = [{"id": "S-001", "text": "\u9ad8\u8840\u58d3", "text_raw": None}]
        out, recs = corrector.correct_segments(segs)
        self.assertEqual(out[0]["text"], "Hypertension")
        self.assertEqual(out[0]["text_raw"], "\u9ad8\u8840\u58d3")
        self.assertEqual(len(recs), 1)


class SuggestTest(unittest.TestCase):
    def test_chief_candidate_strips_noise(self):
        self.assertEqual(
            chief_candidate("\u75c5\u4eba\u4eca\u65e5\u767c\u71b1\uff0cCough\u4e09\u5929"),
            "\u767c\u71b1\u3001Cough\u4e09\u5929",
        )

    def test_chief_candidate_caps_length(self):
        long_text = "\u4e00" * 40 + "\uff0c" + "\u4e8c" * 40
        self.assertLessEqual(len(chief_candidate(long_text)), 24)

    def test_suggest_only_when_missing(self):
        report = {"fields": [
            _field("chief_complaint", ""),
            _field("history_of_present_illness", "\u75c5\u4eba\u4eca\u65e5\u767c\u71b1\uff0cCough\u4e09\u5929"),
        ]}
        out = suggest_missing(report)
        self.assertIn("chief_complaint", out)
        self.assertEqual(out["chief_complaint"]["value"], "\u767c\u71b1\u3001Cough\u4e09\u5929")

        report["fields"][0]["final_value"] = "\u5df2\u586b\u5199"
        self.assertEqual(suggest_missing(report), {})

    def test_suggest_when_engine_says_not_mentioned(self):
        report = {"fields": [
            _field("chief_complaint", "\u672a\u63d0\u53ca"),
            _field("history_of_present_illness", "\u767c\u71b1\u4e09\u5929"),
        ]}
        out = suggest_missing(report)
        self.assertIn("chief_complaint", out)
        self.assertEqual(out["chief_complaint"]["value"], "\u767c\u71b1\u4e09\u5929")

    def test_assessment_hints_skip_negation(self):
        hints = assessment_hints("\u5426\u8a8d\u80ba\u708e\uff0c\u6709\u9ad8\u8840\u58d3\u75c5\u53f2")
        self.assertIn("\u9ad8\u8840\u58d3", hints)
        self.assertNotIn("\u80ba\u708e", hints)

    def test_assessment_suggest_only_when_empty(self):
        report = {"fields": [
            _field("assessment", "\u672a\u63d0\u53ca"),
            _field("history_of_present_illness", "\u9ad8\u8840\u58d3\u75c5\u53f2\u5341\u5e74"),
        ]}
        out = suggest_missing(report)
        self.assertIn("assessment", out)
        self.assertEqual(out["assessment"]["value"], "")
        self.assertIn("\u9ad8\u8840\u58d3", out["assessment"]["options"])

        report["fields"][0]["final_value"] = "\u9ad8\u8840\u58d3"
        self.assertNotIn("assessment", suggest_missing(report))

    def test_assessment_no_hint_without_disease(self):
        report = {"fields": [
            _field("assessment", "\u672a\u63d0\u53ca"),
            _field("history_of_present_illness", "\u767c\u71b1\u4e09\u5929"),
        ]}
        self.assertNotIn("assessment", suggest_missing(report))

    def test_suggest_empty_when_no_source(self):
        report = {"fields": [_field("chief_complaint", ""),
                             _field("history_of_present_illness", "")]}
        self.assertEqual(suggest_missing(report), {})
        self.assertEqual(suggest_missing(None), {})


class HistoryFilterTest(unittest.TestCase):
    """会话列表搜索：关键词、报告状态、时间窗（纯函数，不连库）。"""

    def _row(self, sid: str, payload: str = "", *, updated: str = "2026-10-29T10:00:00+00:00",
             title: str = "", report_id: str | None = None) -> dict:
        return {
            "id": sid, "title": title, "updated_at": updated,
            "created_at": updated, "report_id": report_id, "report_payload": payload,
        }

    def test_search_hits_report_body(self):
        from app.history import filter_sessions

        rows = [
            self._row("ses_a", json.dumps({"status": "signed", "fields": [
                {"key": "chief_complaint", "ai_value": "\u767c\u71b1\u4e09\u5929"}]},
                ensure_ascii=False)),
            self._row("ses_b", json.dumps({"status": "draft", "fields": [
                {"key": "chief_complaint", "ai_value": "\u8179\u75db"}]}, ensure_ascii=False)),
        ]
        hits = filter_sessions(rows, q="\u767c\u71b1")
        self.assertEqual([r["id"] for r in hits], ["ses_a"])

    def test_status_buckets(self):
        from app.history import filter_sessions

        rows = [
            self._row("ses_signed", json.dumps({"status": "signed"})),
            self._row("ses_draft", json.dumps({"status": "draft"})),
            self._row("ses_none", ""),
        ]
        self.assertEqual([r["report_status"] for r in filter_sessions(rows)],
                         ["signed", "draft", "none"])
        self.assertEqual([r["id"] for r in filter_sessions(rows, status="signed")], ["ses_signed"])
        self.assertEqual([r["id"] for r in filter_sessions(rows, status="none")], ["ses_none"])
        self.assertEqual(len(filter_sessions(rows, status="draft")), 1)

    def test_days_window(self):
        from datetime import datetime, timezone

        from app.history import filter_sessions

        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        rows = [
            self._row("ses_fresh", "", updated="2026-10-29T09:00:00+00:00"),
            self._row("ses_old", "", updated="2026-09-01T09:00:00+00:00"),
        ]
        self.assertEqual([r["id"] for r in filter_sessions(rows, days=7, now=now)], ["ses_fresh"])
        self.assertEqual(len(filter_sessions(rows, days=0, now=now)), 2)

    def test_newest_first_and_payload_stripped(self):
        from app.history import filter_sessions

        rows = [
            self._row("ses_old", "", updated="2026-10-01T09:00:00+00:00"),
            self._row("ses_new", json.dumps({"status": "signed"}), updated="2026-10-29T09:00:00+00:00"),
        ]
        out = filter_sessions(rows)
        self.assertEqual([r["id"] for r in out], ["ses_new", "ses_old"])
        self.assertNotIn("report_payload", out[0])


class ExportFormatTest(unittest.TestCase):
    def test_keeps_available_formats(self):
        from app.service import prune_formats

        available, skipped = prune_formats(["text", "json"])
        self.assertEqual(available, ["text", "json"])
        self.assertEqual(skipped, [])

    def test_drops_pdf_when_dependency_missing(self):
        from app.service import prune_formats

        has_pdf = importlib.util.find_spec("reportlab") is not None
        available, skipped = prune_formats(["text", "pdf"])
        if has_pdf:
            self.assertEqual(available, ["text", "pdf"])
            self.assertEqual(skipped, [])
        else:
            self.assertEqual(available, ["text"])
            self.assertEqual(skipped[0]["format"], "pdf")


class TermOverrideTest(unittest.TestCase):
    """本地补丁层（terms/overrides.json）与它编译出的真实词表。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "import_terms", ROOT / "scripts" / "import_terms.py"
        )
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _entry(self, term_id: str, yue: str = "", synonyms: str = "") -> dict:
        return {"term_id": term_id, "category": "化验", "zh": "呼吸频率", "en": "Respiratory rate",
                "yue": yue, "jyutping": "", "type_code": "LAB", "synonyms": synonyms, "note": ""}

    def test_apply_overrides_semantics(self):
        rows = [self._entry("LAB-003", yue="呼吸")]
        stats = self.mod.apply_overrides(rows, {
            "add_terms": [{"term_id": "LOCAL-X", "zh": "阿司匹林", "en": "Aspirin",
                           "yue": "阿斯匹林", "type_code": "DRU"}],
            "add_aliases": [{"term_id": "LAB-003", "alias": "呼吸頻率"}],
            "drop_aliases": [{"term_id": "LAB-003", "alias": "呼吸"}],
        })
        self.assertEqual(stats["warnings"], [])
        self.assertIn("LOCAL-X", stats["added_terms"])
        self.assertIn("LAB-003:呼吸", stats["dropped_aliases"])
        self.assertEqual(rows[0]["yue"], "")
        self.assertIn("呼吸頻率", rows[0]["synonyms"])
        self.assertEqual(rows[1]["term_id"], "LOCAL-X")

    def test_override_duplicate_is_warned(self):
        rows = [self._entry("LAB-003")]
        stats = self.mod.apply_overrides(rows, {
            "add_terms": [{"term_id": "LAB-003", "zh": "x", "en": "y"}],
            "drop_aliases": [{"term_id": "LAB-003", "alias": "不存在"}],
        })
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("冲突" in w for w in stats["warnings"]))
        self.assertTrue(any("未找到别名" in w for w in stats["warnings"]))

    def test_built_terms_reflect_overrides(self):
        seed = json.loads((ROOT / "terms" / "seed_terms.json").read_text(encoding="utf-8"))
        idx = {e["id"]: e for e in seed["entries"]}
        self.assertNotIn("呼吸", idx["lab-003"]["aliases"])
        self.assertIn("呼吸頻率", idx["lab-003"]["aliases"])
        self.assertIn("阿斯匹林", idx["local-drug-001"]["aliases"])

    def test_corrector_keeps_breath_sound(self):
        seed = json.loads((ROOT / "terms" / "seed_terms.json").read_text(encoding="utf-8"))
        corrector = TermCorrector(seed["entries"], seed["version"])
        text = "\u96d9\u80ba\u547c\u5438\u97f3\u7c97\uff0c\u963f\u65af\u5339\u6797\u6bcf\u65e5\u4e00\u6b21\uff0c\u547c\u5438\u983b\u738720\u6b21"
        fixed, recs = corrector.correct_text("S-001", text)
        # 「呼吸音粗」有自己的词条，应替换成 Coarse breath sounds，而不是 Respiratory rate
        self.assertIn("Coarse breath sounds", fixed)
        self.assertIn("Aspirin", fixed)
        self.assertIn("Respiratory rate", fixed)
        for rec in recs:
            self.assertFalse("音" in rec["before"] and "Respiratory" in rec["after"])
            self.assertFalse("音" in rec["before"] and "呼吸" == rec["before"])


# ===========================================================================
# 1b. 长段落切分（长 ASR 段落 -> 短句片段）
# ===========================================================================
class TextSplitTest(unittest.TestCase):
    def test_short_segment_untouched(self):
        segs = [{"id": "S-001", "index": 0, "start_ms": 0, "end_ms": 3000,
                 "text": "病人今日有發燒，血壓高"}]
        out = refine_segments(segs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "S-001")
        self.assertEqual(out[0]["text"], "病人今日有發燒，血壓高")

    def test_long_segment_split_and_timed(self):
        text = "病人今日發熱﹐咳嗽三天﹐有高血壓病史十年﹐長期口服阿斯匹林﹐體查體溫38度5"
        out = refine_segments([{"id": "S-001", "index": 0, "start_ms": 0, "end_ms": 20000,
                                "text": text}])
        self.assertGreater(len(out), 1)
        self.assertEqual([s["id"] for s in out], [f"S-{i+1:03d}" for i in range(len(out))])
        # 内容不丢字，只多了中文标点替换
        joined = "".join(s["text"] for s in out)
        self.assertEqual(len(joined), len(text.replace("﹐", "，")))
        # 时间戳连续、首尾对齐
        self.assertEqual(out[0]["start_ms"], 0)
        self.assertEqual(out[-1]["end_ms"], 20000)
        for prev, cur in zip(out, out[1:]):
            self.assertEqual(prev["end_ms"], cur["start_ms"])

    def test_small_comma_normalised(self):
        out = refine_segments([{"id": "S-001", "index": 0, "start_ms": 0, "end_ms": 1000,
                                "text": "血壓160﹐95"}])
        self.assertIn("，", out[0]["text"])
        self.assertNotIn("﹐", out[0]["text"])


# ===========================================================================
# 1c. 引擎输出后处理：体征内容归位到体格检查
# ===========================================================================
def _field(key: str, ai_value: str, final_value: str = "", required: bool = False) -> dict:
    return {"key": key, "label": key, "section": "O", "required": required,
            "ai_value": ai_value, "final_value": final_value, "sources": [],
            "flags": [], "evidence_ok": bool(ai_value), "note": ""}


class PostprocessTest(unittest.TestCase):
    def _report(self, inv: str, pe: str = "", inv_final: str = "") -> dict:
        return {"id": "rep_x", "fields": [
            _field("physical_examination", pe),
            _field("investigations", inv, final_value=inv_final),
        ], "missing_required": []}

    def test_vital_moved_to_physical_exam(self):
        report, moves = rebalance_report(self._report("體查體溫38度5，", "160，95，"))
        fields = {f["key"]: f for f in report["fields"]}
        self.assertIn("體溫", fields["physical_examination"]["ai_value"])
        self.assertIn("160", fields["physical_examination"]["ai_value"])
        self.assertEqual(fields["investigations"]["ai_value"], "")
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["from"], "investigations")

    def test_lab_fragment_stays(self):
        report, moves = rebalance_report(
            self._report("血常規 WBC 12，；體溫38度5，")
        )
        fields = {f["key"]: f for f in report["fields"]}
        self.assertIn("血常規", fields["investigations"]["ai_value"])
        self.assertNotIn("體溫", fields["investigations"]["ai_value"])
        self.assertIn("體溫", fields["physical_examination"]["ai_value"])
        self.assertEqual(len(moves), 1)

    def test_no_move_without_vital(self):
        report, moves = rebalance_report(self._report("抽血檢查血常規和肝功能，"))
        self.assertEqual(moves, [])
        self.assertEqual([f["key"] for f in report["fields"]],
                         ["physical_examination", "investigations"])

    def test_manual_edit_respected(self):
        report, moves = rebalance_report(
            self._report("體溫38度5，", inv_final="医生已改写")
        )
        self.assertEqual(moves, [])
        fields = {f["key"]: f for f in report["fields"]}
        self.assertEqual(fields["investigations"]["ai_value"], "體溫38度5，")

    def test_hide_rejected_source(self):
        report = self._report("體查體溫38度5，")
        report["reviews"] = [{"field_key": "investigations", "action": "reject",
                              "reviewer": "system:rebalance"}]
        shown = hide_rebalanced_source(report)
        fields = {f["key"]: f for f in shown["fields"]}
        self.assertEqual(fields["investigations"]["ai_value"], "")
        self.assertIn("体征已移至体格检查", fields["investigations"]["note"])

    def test_hide_placeholder_mark(self):
        report = self._report("體查體溫38度5，")
        report["fields"][1]["final_value"] = EMPTY_MARK
        report["reviews"] = [{"field_key": "investigations", "action": "edit",
                              "reviewer": "system:rebalance"}]
        shown = hide_rebalanced_source(report)
        fields = {f["key"]: f for f in shown["fields"]}
        self.assertEqual(fields["investigations"]["ai_value"], "")

    def test_hide_noop_for_manual_reject(self):
        report = self._report("體溫38度5，")
        report["reviews"] = [{"field_key": "investigations", "action": "reject",
                              "reviewer": "dr.zhao"}]
        shown = hide_rebalanced_source(report)
        fields = {f["key"]: f for f in shown["fields"]}
        self.assertEqual(fields["investigations"]["ai_value"], "體溫38度5，")


# ===========================================================================
# 2. 音频规则
# ===========================================================================
class AudioRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mshk-audio-test-"))
        self.cfg = Settings()

    def _write(self, data: bytes) -> Path:
        path = self.tmp / "a.wav"
        path.write_bytes(data)
        return path

    def test_silence_rejected(self):
        res = validate_audio(self.cfg, self._write(silence_wav(12)))
        self.assertFalse(res["ok"])
        self.assertTrue(any("未检测到有效语音" in r for r in res["reasons"]))

    def test_short_audio_warns_but_passes(self):
        res = validate_audio(self.cfg, self._write(make_wav(4)))
        self.assertTrue(res["ok"])
        self.assertTrue(res["warnings"])

    def test_over_long_rejected(self):
        cfg = Settings()
        cfg.max_duration_ms = 1000
        res = validate_audio(cfg, self._write(make_wav(3)))
        self.assertFalse(res["ok"])
        self.assertTrue(any("上限" in r for r in res["reasons"]))

    def test_normal_audio_ok(self):
        res = validate_audio(self.cfg, self._write(make_wav(12)))
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["duration_ms"], 11900)


# ===========================================================================
# 3. 端到端
# ===========================================================================
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn

        cls.asr_srv, cls.asr_url = serve(AsrHandler)
        cls.engine_srv, cls.engine_url = serve(EngineHandler)

        cls.data_dir = Path(tempfile.mkdtemp(prefix="mshk-e2e-"))
        os.environ["MSHK_ASR_URL"] = cls.asr_url
        os.environ["MSHK_ENGINE_URL"] = cls.engine_url
        os.environ["MSHK_DATA_DIR"] = str(cls.data_dir)
        os.environ["MSHK_DEFAULT_TEMPLATE"] = "general_soap"
        os.environ["MSHK_ASR_TIMEOUT_S"] = "30"

        # 必须在环境变量就绪后再构造 settings / app
        import importlib

        from app import config as config_mod

        importlib.reload(config_mod)
        cls.cfg = config_mod.settings

        from app.db import Store
        from app.service import IntegrationService

        cls.store = Store(cls.cfg.db_path)
        cls.service = IntegrationService(cls.cfg, cls.store)

        from app import main as main_mod

        importlib.reload(main_mod)
        cls.app = main_mod.create_app()
        cls.app.state.service = cls.service

        cls.port = free_port()
        cls.server = uvicorn.Server(
            uvicorn.Config(cls.app, host="127.0.0.1", port=cls.port, log_level="warning")
        )
        threading.Thread(target=cls.server.run, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{cls.base}/health", timeout=1):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("集成层未能在 20s 内启动")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.asr_srv.shutdown()
        cls.engine_srv.shutdown()

    # ---------------------------------------------------------------- HTTP
    def _req(self, method: str, path: str, payload=None, raw: bytes | None = None,
             ctype: str | None = None):
        data, headers = None, {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw
            headers["Content-Type"] = ctype or "application/octet-stream"
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method)
        # 本机 Windows 下 localhost 偶发 ConnectionResetError（WinError 10054）突发，
        # 与业务代码无关（纯 http.server / uvicorn / urllib 之间亦复现），做有界重试。
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read()
                    return resp.status, json.loads(body) if body[:1] in (b"{", b"[") else body.decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                body = exc.read()
                try:
                    return exc.code, json.loads(body)
                except json.JSONDecodeError:
                    return exc.code, body.decode("utf-8", "replace")
            except (ConnectionResetError, urllib.error.URLError) as exc:
                if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, ConnectionResetError):
                    raise
                if attempt < 2:
                    time.sleep(0.8)
                    continue
                raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def _upload(self, session_id: str, wav: bytes):
        boundary = "----testboundary"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="visit.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
            + wav + b"\r\n"
            + f"--{boundary}--\r\n".encode()
        )
        return self._req(
            "POST", f"/sessions/{session_id}/audio", raw=body,
            ctype=f"multipart/form-data; boundary={boundary}",
        )

    # ---------------------------------------------------------------- 用例
    def test_health_reports_components(self):
        status, body = self._req("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        names = {c["name"] for c in body["components"]}
        self.assertEqual(names, {"asr", "engine"})
        self.assertGreater(body["terms"]["entries"], 0)

    def test_full_flow(self):
        AsrState.probability = 0.92
        AsrState.registered = False  # 强制走"首次注册再登录"路径

        status, session = self._req("POST", "/sessions",
                                    {"template_id": "general_soap", "title": "E2E"})
        self.assertEqual(status, 200)
        sid = session["id"]
        self.assertEqual(session["status"], "created")

        # --- 录音校验：静音被拒
        status, body = self._upload(sid, silence_wav(12))
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        status, detail = self._req("GET", f"/sessions/{sid}")
        self.assertEqual(detail["session"]["status"], "created")

        # --- 正常音频
        status, body = self._upload(sid, make_wav(12))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreater(body["duration_ms"], 11900)

        # --- 转写
        status, body = self._req("POST", f"/sessions/{sid}/transcribe", {"language": "yue"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["segments"]), 3)
        self.assertEqual(body["segments"][0]["id"], "S-001")
        self.assertEqual(body["segments"][0]["start_ms"], 0)
        self.assertEqual(body["segments"][1]["start_ms"], 4000)
        self.assertFalse(body["segments"][0]["low_confidence"])

        # --- 人工修正（刻意用繁体，验证繁→简折叠后仍能命中简体别名）
        status, seg = self._req("PATCH", f"/sessions/{sid}/segments/S-001", {"text": "病人今日有發燒，有高血壓，體溫38.5度"})
        self.assertEqual(status, 200)
        self.assertTrue(seg["edited"])

        # --- 术语纠正
        status, body = self._req("POST", f"/sessions/{sid}/correct-terms", {})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["warnings"], body)
        status, detail = self._req("GET", f"/sessions/{sid}")
        self.assertEqual(detail["session"]["status"], "corrected")
        texts = [s["text"] for s in detail["segments"]]
        self.assertIn("Hypertension", texts[0])
        self.assertIn("Fever", texts[0])
        self.assertEqual(detail["segments"][0]["text_raw"], "病人今日有發燒，有高血壓，體溫38.5度")

        # --- 撤销
        status, body = self._req("POST", f"/sessions/{sid}/correct-terms/undo", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["restored_segments"], 1)
        status, detail = self._req("GET", f"/sessions/{sid}")
        self.assertIn("高血壓", detail["segments"][0]["text"])

        # --- 再纠正一次，进入抽取
        self._req("POST", f"/sessions/{sid}/correct-terms", {})

        status, body = self._req("POST", f"/sessions/{sid}/extract", {"deidentify": True})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["elements"]), 3)
        self.assertIn("stub-extract", body["pipeline"])
        # 引擎抽的是中文关键词规则：送过去的必须是纠正前原文，而不是 Hypertension/Fever
        sent = " ".join(EngineState.last_segments)
        self.assertIn("高血壓", sent)
        self.assertNotIn("Hypertension", sent)

        # --- 生成
        status, body = self._req("POST", f"/sessions/{sid}/generate",
                                 {"template_id": "general_soap"})
        self.assertEqual(status, 200, body)
        report = body["report"]
        rid = report["id"]
        self.assertEqual(report["template_id"], "general_soap")
        self.assertEqual(len(report["fields"]), 4)
        self.assertNotEqual(body["elements"], [])

        # --- 校审：编辑 SUBJECTIVE
        status, report = self._req(
            "PATCH", f"/reports/{rid}/fields/SUBJECTIVE",
            {"action": "edit", "value": "病人今日有發燒，血壓高", "reviewer": "dr.test"},
        )
        self.assertEqual(status, 200, report)
        subj = [f for f in report["fields"] if f["key"] == "SUBJECTIVE"][0]
        self.assertEqual(subj["final_value"], "病人今日有發燒，血壓高")

        # --- 签署
        status, body = self._req("POST", f"/reports/{rid}/sign", {"signed_by": "dr.test"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["report"]["status"], "signed")

        # --- 导出
        status, body = self._req("POST", f"/reports/{rid}/export",
                                 {"formats": ["text", "json"], "language_mode": "bilingual"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["records"]), 2)
        for record in body["records"]:
            self.assertTrue(record["local_path"])
            path = self.cfg.export_dir / record["local_path"]
            self.assertTrue(path.is_file(), path)

        # --- 缺依赖的格式被跳过，而不是整单失败
        if importlib.util.find_spec("reportlab") is None:
            status, body = self._req("POST", f"/reports/{rid}/export",
                                     {"formats": ["text", "pdf"], "language_mode": "bilingual"})
            self.assertEqual(status, 200, body)
            self.assertEqual([r["format"] for r in body["records"]], ["text"])
            self.assertEqual([s["format"] for s in body["skipped"]], ["pdf"])

        # --- 导出文件可下载
        name = body["records"][0]["local_path"]
        with urllib.request.urlopen(f"{self.base}/exports/{name}", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertGreater(len(resp.read()), 0)

        # --- 最终状态与审计
        status, detail = self._req("GET", f"/sessions/{sid}")
        self.assertEqual(detail["session"]["status"], "exported")
        status, audit = self._req("GET", f"/api/audit?session_id={sid}&limit=100")
        events = {a["event_type"] for a in audit}
        for expected in ("session_created", "audio_rejected", "audio_stored", "transcribed",
                         "terms_corrected", "terms_undone", "extracted",
                         "report_generated", "field_reviewed", "report_signed",
                         "report_exported"):
            self.assertIn(expected, events)

    def test_low_confidence_gate_and_confirm(self):
        """BR-09：低置信度片段未核对时不得进入抽取定稿。"""
        AsrState.probability = 0.5
        status, session = self._req("POST", "/sessions", {"template_id": "general_soap"})
        sid = session["id"]
        self._upload(sid, make_wav(12))
        status, body = self._req("POST", f"/sessions/{sid}/transcribe", {"language": "yue"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["low_confidence"]), 3)

        status, body = self._req("POST", f"/sessions/{sid}/extract", {})
        self.assertEqual(status, 400)
        self.assertIn("低置信度", body["detail"])

        # 核对其中一段后可继续，并带回警告
        self._req("PATCH", f"/sessions/{sid}/segments/S-001", {"confirmed": True})
        status, body = self._req("POST", f"/sessions/{sid}/extract", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["elements"]), 1)
        self.assertTrue(any("低置信度" in w for w in body["warnings"]))
        AsrState.probability = 0.92

    def test_export_blocked_before_sign(self):
        """BR-10 / AC-08：未签署（必填缺失）时签署被阻止，导出仍由引擎拒绝。"""
        status, session = self._req("POST", "/sessions", {"template_id": "general_soap"})
        sid = session["id"]
        # 只上传一段包含 P 类文本的音频，制造 SUBJECTIVE/ASSESSMENT 缺失
        AsrState.segments = [{"id": 0, "start": 0.0, "end": 12.0, "text": "建議覆診"}]
        self._upload(sid, make_wav(12))
        self._req("POST", f"/sessions/{sid}/transcribe", {"language": "yue"})
        status, body = self._req("POST", f"/sessions/{sid}/generate", {})
        self.assertEqual(status, 200, body)
        rid = body["report"]["id"]
        self.assertTrue(body["report"]["missing_required"])

        status, body = self._req("POST", f"/reports/{rid}/sign", {"signed_by": "dr.test"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertTrue(body["blocked_by"])

        status, detail = self._req("GET", f"/sessions/{sid}")
        statuses = [a["event_type"] for a in
                    (self._req("GET", f"/api/audit?session_id={sid}")[1])]
        self.assertIn("sign_blocked", statuses)
        self.assertNotEqual(detail["session"]["status"], "signed")

        AsrState.segments = AsrState.segments  # 复原由下一个用例负责
        AsrState.segments = [
            {"id": 0, "start": 0.0, "end": 4.0, "text": "病人今日有發燒，血壓高"},
            {"id": 1, "start": 4.0, "end": 8.0, "text": "照咗胸肺片，無明顯異常"},
            {"id": 2, "start": 8.0, "end": 12.0, "text": "建議食退燒藥，兩日後覆診"},
        ]

    def test_delete_session_cascades(self):
        status, session = self._req("POST", "/sessions", {"title": "to-delete"})
        sid = session["id"]
        self._upload(sid, make_wav(12))
        status, body = self._req("DELETE", f"/sessions/{sid}")
        self.assertEqual(status, 200)
        self.assertTrue(body["deleted"])
        self.assertTrue(body["audio_removed"])
        status, body = self._req("GET", f"/sessions/{sid}")
        self.assertEqual(status, 404)
        self.assertFalse((self.cfg.audio_dir / f"{sid}.wav").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
