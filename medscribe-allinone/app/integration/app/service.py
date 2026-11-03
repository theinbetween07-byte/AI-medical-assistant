"""会话编排服务：把 ASR、术语库、报告引擎串成 PRD 的 S1→E 流程。

职责边界（对应《技术设计》1.2 组件职责）：
    音频管理      -> 转码、时长/静音校验、文件命名
    ASR 适配层    -> 调上游、分段映射、低置信度标注
    术语纠正      -> 本地词表 + 可撤销记录
    要素抽取      -> 调引擎 /api/generate（首次同时产出初稿快照）
    报告生成      -> 复用已抽要素，按模板出初稿
    校审/签署/导出 -> 透传引擎，并同步快照与审计
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from . import audio as audio_mod
from .asr_client import ASRClient, ASRError, map_segments
from .postprocess import EMPTY_MARK, hide_rebalanced_source, rebalance_report
from .history import filter_sessions
from .suggest import suggest_missing
from .textsplit import refine_segments
from .config import Settings
from .db import Store
from .engine_client import EngineClient, EngineError
from .schemas import (
    AudioValidation,
    Session,
    SessionDetail,
    Segment,
    utcnow_iso,
)
from .terms import TermCorrector
from .tc_sc import has_traditional


class NotFound(RuntimeError):
    pass


class InvalidState(RuntimeError):
    pass

class UpstreamError(RuntimeError):
    pass


def prune_formats(formats: list[str]) -> tuple[list[str], list[dict]]:
    """剔除当前环境不可用的导出格式（如缺 reportlab 时的 pdf），返回可用格式与被跳过项。"""
    available: list[str] = []
    skipped: list[dict] = []
    for fmt in formats:
        if fmt == "pdf" and importlib.util.find_spec("reportlab") is None:
            skipped.append({"format": "pdf", "reason": "缺少 reportlab 依赖，PDF 导出不可用"})
            continue
        available.append(fmt)
    return available, skipped


STATUS_ORDER = [
    "created",
    "recorded",
    "transcribed",
    "corrected",
    "extracted",
    "generated",
    "signed",
    "exported",
]


def _rank(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return -1


class IntegrationService:
    def __init__(self, cfg: Settings, store: Optional[Store] = None) -> None:
        self.cfg = cfg
        cfg.ensure_dirs()
        self.store = store or Store(cfg.db_path)
        self.asr = ASRClient(cfg)
        self.engine = EngineClient(cfg)
        self.corrector = TermCorrector.load(cfg.terms_dir)

    # --------------------------------------------------------------- helpers
    def reload_terms(self) -> dict:
        self.corrector = TermCorrector.load(self.cfg.terms_dir)
        try:
            self.engine.reload()
        except EngineError:
            pass
        return {"version": self.corrector.version, "entries": len(self.corrector.entries)}

    def _audit(self, session_id: str, event: str, payload: dict) -> None:
        self.store.add_audit(session_id, event, payload, utcnow_iso())

    def _row_to_session(self, row: dict) -> Session:
        data = dict(row)
        if isinstance(data.get("warnings"), str):
            try:
                data["warnings"] = json.loads(data["warnings"] or "[]")
            except json.JSONDecodeError:
                data["warnings"] = []
        data.pop("deleted_at_flag", None)
        return Session(**{k: v for k, v in data.items() if k in Session.model_fields})

    def _require(self, session_id: str) -> Session:
        row = self.store.get_session(session_id)
        if not row or row.get("deleted_at"):
            raise NotFound(f"会话不存在：{session_id}")
        return self._row_to_session(row)

    def _save(self, session: Session) -> Session:
        session.updated_at = utcnow_iso()
        self.store.upsert_session(session.model_dump())
        return session

    def _push_rebalanced(self, session_id: str, report: dict, moves: list[dict]) -> dict:
        """把归位结果同步回引擎。

        否则引擎侧仍是原字段，任何 `/review`、`/sign` 返回的整份报告都会覆盖本地快照，
        归位等于白做；导出/预览也直接读引擎，必须让引擎自己也改。
        """
        report_id = report.get("id")
        fields = {f.get("key"): f for f in report.get("fields") or []}
        if not report_id:
            return report
        latest = report
        try:
            for key in ("physical_examination", "investigations"):
                field = fields.get(key)
                if not field:
                    continue
                value = str(field.get("ai_value") or "").strip()
                # 引擎 final_value 为空会回落到 ai_value，且不接受空值 edit；
                # 用「—」作为无内容占位，导出才不会把搬走的体征又打一遍。
                latest = self.engine.review_field(
                    report_id, key, "edit", value or EMPTY_MARK, "system:rebalance"
                )
                self.store.save_report(session_id, latest, utcnow_iso())
        except EngineError as exc:
            # 同步失败时保留本地归位结果，并留审计，人工仍可在校审页改
            self._audit(session_id, "field_rebalance_sync_failed",
                        {"report_id": report_id, "error": str(exc)})
        return hide_rebalanced_source(latest)

    def _need(self, session: Session, minimum: str, action: str) -> None:
        if _rank(session.status) < _rank(minimum):
            raise InvalidState(
                f"当前会话状态为 {session.status}，{action}前需要先完成上一步（{minimum}）"
            )

    def _segments(self, session_id: str) -> list[dict]:
        return self.store.list_segments(session_id)

    def _usable_segments(self, session_id: str) -> tuple[list[dict], list[str]]:
        """BR-09：低置信度且未核对的分段不进入抽取定稿。"""
        usable, held = [], []
        for seg in self._segments(session_id):
            if seg["low_confidence"] and not seg["confirmed"]:
                held.append(seg["id"])
            else:
                usable.append(seg)
        warnings = []
        if held:
            warnings.append(
                f"{len(held)} 个低置信度片段尚未核对（{', '.join(held[:5])}"
                f"{'…' if len(held) > 5 else ''}），已暂不进入抽取（BR-09）"
            )
        return usable, warnings

    # ---------------------------------------------------------------- S1 会话
    def create_session(self, template_id: str = "", title: str = "") -> Session:
        now = utcnow_iso()
        session = Session(
            id=f"ses_{uuid.uuid4().hex[:12]}",
            created_at=now,
            updated_at=now,
            status="created",
            title=title or f"会话 {now[:19].replace('T', ' ')}",
            template_id=template_id or self.cfg.default_template,
        )
        self.store.upsert_session(session.model_dump())
        self._audit(session.id, "session_created", {"template_id": session.template_id})
        return session

    def list_sessions(
        self, limit: int = 50, q: str = "", status: str = "", days: int = 0
    ) -> list[Session]:
        rows = self.store.list_sessions_for_search()
        rows = filter_sessions(rows, q=q, status=status, days=days, limit=limit)
        return [self._row_to_session(row) for row in rows]

    def get_detail(self, session_id: str) -> SessionDetail:
        session = self._require(session_id)
        report = hide_rebalanced_source(self.store.load_report(session_id))
        return SessionDetail(
            session=session,
            segments=[Segment(**s) for s in self._segments(session_id)],
            elements=self.store.load_elements(session_id),
            corrections=self.store.load_corrections(session_id),
            report=report,
            exports=self.store.list_exports(session_id),
            suggestions=suggest_missing(report),
        )

    def delete_session(self, session_id: str) -> dict:
        row = self.store.get_session(session_id)
        if not row:
            raise NotFound(f"会话不存在：{session_id}")
        report_id = row.get("report_id")
        audio_name = row.get("audio_path")

        removed_audio = False
        if audio_name:
            path = self.cfg.audio_dir / audio_name
            if path.is_file():
                path.unlink()
                removed_audio = True

        engine_deleted = False
        if report_id:
            try:
                self.engine.delete_report(report_id)
                engine_deleted = True
            except EngineError as exc:
                if exc.status != 404:
                    raise UpstreamError(f"删除引擎侧报告失败：{exc}") from exc

        self.store.purge_session(session_id)
        self.store.mark_deleted(session_id, utcnow_iso())
        self._audit(session_id, "session_deleted", {
            "audio_removed": removed_audio,
            "engine_report_deleted": engine_deleted,
        })
        return {
            "deleted": True,
            "session_id": session_id,
            "audio_removed": removed_audio,
            "engine_report_deleted": engine_deleted,
        }

    # ---------------------------------------------------------------- S2 录音
    def save_audio(self, session_id: str, filename: str, content: bytes) -> AudioValidation:
        session = self._require(session_id)
        if _rank(session.status) > _rank("recorded"):
            raise InvalidState(
                "该会话已完成转写，重新录音请新建会话（BR-11：已签署内容锁定）"
            )
        if not content:
            raise InvalidState("上传内容为空")
        if len(content) > 50 * 1024 * 1024:
            raise InvalidState("音频超过 50 MB 上限，请缩短录音（BR-01）")

        suffix = Path(filename or "audio.webm").suffix or ".webm"
        stored_name = f"{session_id}.wav"
        final_path = self.cfg.audio_dir / stored_name

        tmp_dir = Path(tempfile.mkdtemp(prefix="mshk-audio-", dir=str(self.cfg.data_dir)))
        raw_path = tmp_dir / f"input{suffix}"
        try:
            raw_path.write_bytes(content)
            audio_mod.to_wav16k(self.cfg, raw_path, final_path)
            result = audio_mod.validate(self.cfg, final_path)
        except audio_mod.AudioError as exc:
            final_path.unlink(missing_ok=True)
            self._audit(session_id, "audio_rejected", {"reason": str(exc)})
            raise InvalidState(str(exc)) from exc
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if not result["ok"]:
            final_path.unlink(missing_ok=True)
            self._audit(session_id, "audio_rejected", {"reasons": result["reasons"]})
            return AudioValidation(
                ok=False,
                duration_ms=result["duration_ms"],
                rms=result["rms"],
                silent_ratio=result["silent_ratio"],
                reasons=result["reasons"],
                warnings=result["warnings"],
            )

        session.status = "recorded"
        session.audio_filename = Path(filename or "").name
        session.audio_path = stored_name
        session.duration_ms = result["duration_ms"]
        session.warnings = result["warnings"]
        self._save(session)
        self._audit(
            session_id,
            "audio_stored",
            {
                "filename": session.audio_filename,
                "duration_ms": result["duration_ms"],
                "rms": result["rms"],
            },
        )
        return AudioValidation(stored_name=stored_name, **result)

    # -------------------------------------------------------------- S3 转写
    def transcribe(
        self,
        session_id: str,
        language: str = "",
        beam_size: int = 5,
        asr_template: str = "soap",
    ) -> dict:
        session = self._require(session_id)
        self._need(session, "recorded", "转写")
        wav_path = self.cfg.audio_dir / session.audio_path
        if not wav_path.is_file():
            raise InvalidState("找不到会话音频文件，请重新上传录音")

        try:
            result = self.asr.transcribe(
                wav_path,
                language=language or self.cfg.default_language,
                asr_template=asr_template,
                beam_size=beam_size,
            )
        except ASRError as exc:
            self._audit(session_id, "transcribe_failed", {"error": str(exc)})
            raise UpstreamError(f"转写失败，请重试或重录：{exc}") from exc

        mapped = refine_segments(map_segments(result, self.cfg.low_confidence_threshold))
        if not mapped:
            self._audit(session_id, "transcribe_empty", {"asr": result})
            raise UpstreamError("转写失败：未得到任何文本，请重试或重录（AC-13）")

        self.store.replace_segments(session_id, mapped)
        low = [s["id"] for s in mapped if s["low_confidence"]]

        session.status = "transcribed"
        session.language = str(result.get("language") or "")
        session.language_probability = float(result.get("language_probability") or 0.0)
        session.segment_count = len(mapped)
        session.warnings = (session.warnings or []) + (
            [f"{len(low)} 个片段置信度低于 {self.cfg.low_confidence_threshold}，"
             "已高亮，请核对后再抽取（BR-09）"] if low else []
        )
        self._save(session)
        self._audit(
            session_id,
            "transcribed",
            {
                "segments": len(mapped),
                "language": session.language,
                "language_probability": session.language_probability,
                "low_confidence": low,
                "model": result.get("model"),
                "elapsed": result.get("elapsed"),
            },
        )
        return {
            "session": session.model_dump(),
            "segments": [Segment(**s) for s in mapped],
            "low_confidence": low,
            "note": "上游 ASR 未返回逐段置信度，confidence 为按整段语言概率推算的代理值",
        }

    # --------------------------------------------------- 人工修正分段（PATCH）
    def patch_segment(self, session_id: str, segment_id: str, text: Optional[str],
                      confirmed: Optional[bool]) -> dict:
        session = self._require(session_id)
        seg = self.store.get_segment(session_id, segment_id)
        if not seg:
            raise NotFound(f"分段不存在：{session_id}/{segment_id}")
        if _rank(session.status) >= _rank("signed"):
            raise InvalidState("报告已签署，转写内容锁定（BR-11）")

        fields: dict = {}
        if text is not None and text.strip() != seg["text"]:
            fields["text"] = text.strip()
            fields["edited"] = 1
            # 人工修正不写 text_raw：text_raw 只记「术语纠正前的文本」，
            # 用于撤销术语替换而不回退人工编辑（见 terms.correct_segments）
        if confirmed is not None:
            fields["confirmed"] = int(bool(confirmed))
        if fields:
            self.store.update_segment(session_id, segment_id, fields)
            self._audit(
                session_id,
                "segment_patched",
                {"segment_id": segment_id, "fields": sorted(fields)},
            )
        updated = self.store.get_segment(session_id, segment_id)
        return Segment(**{k: v for k, v in updated.items()}).model_dump()

    # --------------------------------------------------------- FR-03 术语纠正
    def correct_terms(self, session_id: str) -> dict:
        session = self._require(session_id)
        self._need(session, "transcribed", "术语纠正")
        if _rank(session.status) >= _rank("signed"):
            raise InvalidState("报告已签署，内容锁定（BR-11）")

        segments = self._segments(session_id)
        warnings: list[str] = []
        trad = sum(1 for s in segments if has_traditional(s.get("text", "")))
        if trad:
            warnings.append(
                f"{trad} 个片段为繁体输出，术语匹配已按简体折叠（原文未被改写）"
            )
        corrected, corrections = self.corrector.correct_segments(segments)
        self.store.replace_segments(session_id, corrected)
        self.store.save_corrections(session_id, corrections, utcnow_iso())
        session.status = "corrected" if _rank(session.status) <= _rank("corrected") else session.status
        self._save(session)
        self._audit(
            session_id,
            "terms_corrected",
            {
                "version": self.corrector.version,
                "corrections": len(corrections),
                "terms": sorted({c["term_id"] for c in corrections if c["term_id"]}),
            },
        )
        return {
            "session": session.model_dump(),
            "version": self.corrector.version,
            "changed_segments": sum(
                1 for a, b in zip(segments, corrected) if a["text"] != b["text"]
            ),
            "corrections": corrections,
            "warnings": warnings,
        }

    def undo_corrections(self, session_id: str) -> dict:
        """一键撤销：用 text_raw 还原（FR-03「可撤销」）。"""
        session = self._require(session_id)
        if _rank(session.status) >= _rank("signed"):
            raise InvalidState("报告已签署，内容锁定（BR-11）")
        segments = self._segments(session_id)
        restored = 0
        for seg in segments:
            if seg.get("text_raw") and seg["text_raw"] != seg["text"]:
                restored += 1
        if restored:
            self.store.replace_segments(
                session_id,
                [
                    {**s, "text": s["text_raw"] or s["text"]}
                    for s in segments
                ],
            )
        self.store.save_corrections(session_id, [], utcnow_iso())
        session.status = "transcribed"
        self._save(session)
        self._audit(session_id, "terms_undone", {"restored_segments": restored})
        return {"restored_segments": restored, "session": session.model_dump()}

    def _engine_segments(self, session_id: str, segments: list[dict]) -> list[dict]:
        """回传给引擎的分段文本：撤销本层已应用的术语替换。

        为什么不在引擎侧直接吃纠正后的英文文本：
        引擎的要素抽取是中文关键词规则，实测把「咳嗽」换成 `Cough`、「呼吸」换成
        `Respiratory rate` 之后，主诉/现病史/计划整段失配，报告只剩体格检查一项。
        本层的纠正只服务于 S2 的「可见、可撤销」（FR-03），引擎会用同一份术语库
        自行标准化并保持句子可读性，因此这里回滚成原始中文再送。
        人工编辑被保留：text_raw 记录的是纠正前文本，回滚只针对替换过的词。
        """
        by_seg: dict[str, list[dict]] = {}
        for item in reversed(self.store.load_corrections(session_id)):
            by_seg.setdefault(item.get("segment_id", ""), []).append(item)

        out: list[dict] = []
        for seg in segments:
            text = seg.get("text", "")
            for corr in by_seg.get(seg["id"], []):
                before, after = corr.get("before", ""), corr.get("after", "")
                if after and after in text:
                    text = text.replace(after, before, 1)
            item = dict(seg)
            item["text"] = text
            item["text_raw"] = None
            out.append(item)
        return out

    # --------------------------------------------------------- FR-04 要素抽取
    def extract(self, session_id: str, deidentify: bool = False) -> dict:
        session = self._require(session_id)
        self._need(session, "transcribed", "要素抽取")
        usable, warnings = self._usable_segments(session_id)
        if not usable:
            raise InvalidState(
                "所有片段均为低置信度且未核对，请先在转写页确认至少一段（BR-09）"
            )

        try:
            result = self.engine.generate(
                session_id=session_id,
                template_id=session.template_id or self.cfg.default_template,
                segments=[
                    Segment(**s).as_engine_segment()
                    for s in self._engine_segments(session_id, usable)
                ],
                elements=None,
                deidentify=deidentify,
                term_correction=True,
                language_mode="bilingual",
            )
        except EngineError as exc:
            self._audit(session_id, "extract_failed", {"error": str(exc)})
            raise UpstreamError(f"要素抽取失败：{exc}") from exc

        elements = result.get("elements") or []
        report = result.get("report") or {}
        report, rebalanced = rebalance_report(report)
        if rebalanced:
            report = self._push_rebalanced(session_id, report, rebalanced)
        self.store.save_elements(session_id, elements, utcnow_iso())
        if report:
            self.store.save_report(session_id, report, utcnow_iso())
            session.report_id = report.get("id")
        session.status = "extracted"
        session.warnings = [*(session.warnings or []), *warnings]
        self._save(session)
        if rebalanced:
            self._audit(session_id, "field_rebalanced", {"moves": rebalanced})
        self._audit(
            session_id,
            "extracted",
            {
                "elements": len(elements),
                "uncategorized": sum(1 for e in elements if e.get("type") == "uncategorized"),
                "deidentified": result.get("deidentified", False),
                "pipeline": result.get("pipeline", []),
            },
        )
        return {
            "session": session.model_dump(),
            "elements": elements,
            "report": report,
            "pipeline": result.get("pipeline", []),
            "warnings": warnings,
        }

    # --------------------------------------------------------- FR-05 生成初稿
    def generate(self, session_id: str, template_id: str = "", deidentify: Optional[bool] = None,
                 language_mode: str = "bilingual", keep_unclassified: bool = True) -> dict:
        session = self._require(session_id)
        self._need(session, "transcribed", "生成报告")
        if _rank(session.status) >= _rank("signed"):
            raise InvalidState("报告已签署，如需修改请另建修订版本（BR-11）")

        target = template_id or session.template_id or self.cfg.default_template
        usable, warnings = self._usable_segments(session_id)
        if not usable:
            raise InvalidState("没有可用于生成的转写片段，请先确认低置信度片段（BR-09）")

        elements = self.store.load_elements(session_id) or None
        engine_segments = [
            Segment(**s).as_engine_segment()
            for s in self._engine_segments(session_id, usable)
        ]
        try:
            result = self.engine.generate(
                session_id=session_id,
                template_id=target,
                segments=engine_segments,
                elements=elements,
                deidentify=self.cfg.deidentify_enabled if deidentify is None else deidentify,
                term_correction=True,
                language_mode=language_mode,
                keep_unclassified=keep_unclassified,
            )
        except EngineError as exc:
            # BR-06：模板不可用时回退到通用 SOAP，并记录事件
            if exc.status == 404 and target != "general_soap":
                self._audit(session_id, "template_fallback", {"from": target})
                result = self.engine.generate(
                    session_id=session_id,
                    template_id="general_soap",
                    segments=engine_segments,
                    elements=elements,
                    deidentify=self.cfg.deidentify_enabled,
                    term_correction=True,
                    language_mode=language_mode,
                )
                warnings = [*warnings, "所选模板不可用，已回退到通用 SOAP 模板（BR-06）"]
                target = "general_soap"
            else:
                self._audit(session_id, "generate_failed", {"error": str(exc)})
                raise UpstreamError(f"生成失败，未修改任何已确认内容：{exc}") from exc

        report = result.get("report") or {}
        report, rebalanced = rebalance_report(report)
        if rebalanced:
            report = self._push_rebalanced(session_id, report, rebalanced)
        self.store.save_elements(session_id, result.get("elements") or [], utcnow_iso())
        self.store.save_report(session_id, report, utcnow_iso())
        session.status = "generated"
        session.template_id = target
        session.report_id = report.get("id")
        session.warnings = [*(session.warnings or []), *warnings]
        self._save(session)
        if rebalanced:
            self._audit(session_id, "field_rebalanced", {"moves": rebalanced})
        self._audit(
            session_id,
            "report_generated",
            {
                "report_id": report.get("id"),
                "template_id": target,
                "mode": report.get("mode"),
                "missing_required": report.get("missing_required", []),
                "warnings": report.get("warnings", []),
            },
        )
        return {
            "session": session.model_dump(),
            "report": report,
            "elements": result.get("elements", []),
            "pipeline": result.get("pipeline", []),
            "warnings": warnings,
        }

    # ------------------------------------------------------- FR-07 字段校审
    def review_field(self, report_id: str, field_key: str, action: str,
                     value: Optional[str], reviewer: str) -> dict:
        session = self._session_by_report(report_id)
        if session and _rank(session.status) >= _rank("signed"):
            raise InvalidState("报告已签署，内容锁定（BR-11）")
        try:
            report = self.engine.review_field(report_id, field_key, action, value, reviewer)
        except EngineError as exc:
            raise UpstreamError(f"字段校审失败：{exc}") from exc
        if session:
            self.store.save_report(session.id, report, utcnow_iso())
            if _rank(session.status) < _rank("generated"):
                session.status = "generated"
                self._save(session)
            self._audit(
                session.id,
                "field_reviewed",
                {"report_id": report_id, "field_key": field_key, "action": action,
                 "reviewer": reviewer},
            )
        return report

    # ----------------------------------------------------------- FR-08 签署
    def sign(self, report_id: str, signed_by: str) -> dict:
        session = self._session_by_report(report_id)
        try:
            result = self.engine.sign(report_id, signed_by)
        except EngineError as exc:
            raise UpstreamError(f"签署失败：{exc}") from exc

        if result.get("ok") and result.get("report"):
            report = result["report"]
            if session:
                self.store.save_report(session.id, report, utcnow_iso())
                session.status = "signed"
                session.report_id = report_id
                self._save(session)
                self._audit(
                    session.id, "report_signed",
                    {"report_id": report_id, "signed_by": signed_by},
                )
        elif session:
            self._audit(
                session.id, "sign_blocked",
                {"report_id": report_id, "blocked_by": result.get("blocked_by", [])},
            )
        return result

    # ----------------------------------------------------------- FR-09 导出
    def export(self, report_id: str, formats: list[str], language_mode: str) -> dict:
        session = self._session_by_report(report_id)
        formats, skipped = prune_formats(formats)
        if not formats:
            raise InvalidState(
                "没有可用的导出格式：" + "；".join(s.get("reason", "") for s in skipped)
            )
        # 幂等：同一报告同一格式只保留一条记录。客户端重试（Windows 连接重置）
        # 或重复点击都不会再产生重复导出。
        existing: dict[str, dict] = {}
        if session:
            existing = {e["format"]: e for e in self.store.list_exports(session.id)}
        pending = [fmt for fmt in formats if fmt not in existing]
        records: list[dict] = []
        if pending:
            try:
                records = self.engine.export(report_id, pending, language_mode)
            except EngineError as exc:
                raise UpstreamError(f"导出失败：{exc}") from exc

        at = utcnow_iso()
        # 引擎把文件写在它自己的 data/exports 下。为了让集成层的界面能直接下载，
        # 这里复制一份到 integration/data/exports（导出失败不影响引擎侧记录）。
        local_records: list[dict] = []
        for record in records:
            item = dict(record)
            src = Path(record.get("path", "")) if record.get("path") else None
            if src and src.is_file():
                try:
                    self.cfg.export_dir.mkdir(parents=True, exist_ok=True)
                    dst = self.cfg.export_dir / src.name
                    shutil.copyfile(src, dst)
                    item["local_path"] = dst.name
                except OSError as exc:
                    item["local_path"] = ""
                    item["copy_error"] = str(exc)
            local_records.append(item)

        if session:
            if local_records:
                for record in local_records:
                    self.store.add_export(session.id, record, at)
                session.status = "exported" if _rank(session.status) >= _rank("signed") else session.status
                self._save(session)
                self._audit(
                    session.id, "report_exported",
                    {"report_id": report_id, "formats": [r.get("format") for r in local_records],
                     "skipped": skipped},
                )
        merged = [existing[fmt] for fmt in formats if fmt in existing] + local_records
        return {"report_id": report_id, "records": merged, "skipped": skipped}

    def preview(self, report_id: str, fmt: str, language_mode: str) -> str:
        try:
            return self.engine.preview(report_id, fmt, language_mode)
        except EngineError as exc:
            raise UpstreamError(f"预览失败：{exc}") from exc

    def _session_by_report(self, report_id: str) -> Optional[Session]:
        rows = self.store.list_sessions(limit=200, include_deleted=True)
        for row in rows:
            if row.get("report_id") == report_id:
                return self._row_to_session(row)
        return None

    # ------------------------------------------------------------------ 其它
    def templates(self) -> list[dict]:
        try:
            items = self.engine.templates()
        except EngineError as exc:
            raise UpstreamError(f"读取模板失败：{exc}") from exc
        return items

    def glossary(self, keyword: str = "", limit: int = 200) -> dict:
        path = self.cfg.terms_dir / "glossary_full.json"
        if not path.is_file():
            return {"version": "", "entries": [], "total": 0}
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
        if keyword:
            needle = keyword.lower()
            entries = [
                e for e in entries
                if needle in e.get("term_norm", "").lower()
                or needle in e.get("zh", "")
                or needle in e.get("yue", "")
                or any(needle in a.lower() for a in e.get("aliases", []))
            ]
        return {
            "version": payload.get("version", ""),
            "total": len(payload.get("entries", [])),
            "matched": len(entries),
            "entries": entries[:limit],
        }

    def audit(self, session_id: str = "", limit: int = 200) -> list[dict]:
        return self.store.list_audit(session_id, limit)

    # ------------------------------------------------------------ ASR 模型切换
    def asr_models(self) -> dict:
        """列出上游支持的 Whisper 型号、本地已下载集合与当前加载型号。"""
        try:
            info = self.asr.models()
        except ASRError as exc:
            raise UpstreamError(f"读取模型列表失败：{exc}") from exc
        try:
            health = self.asr.health()
        except ASRError:
            health = {}
        supported = list(info.get("supported") or [])
        local = list(info.get("local") or [])
        # 兼容舊版上游（字符串列表）與新版（帶 installed/install 的對象）
        normalised = [
            {"id": m, "label": m, "kind": "whisper", "local": m in local, "installed": m in local}
            if isinstance(m, str)
            else m
            for m in supported
        ]
        return {
            "supported": normalised,
            "local": local,
            "current": info.get("current") or health.get("model") or "",
            "engine": info.get("engine") or "whisper",
            "device": health.get("device", ""),
            "compute_type": health.get("compute_type", ""),
        }

    def select_asr_model(self, model: str) -> dict:
        if not model:
            raise InvalidState("缺少模型标识")
        try:
            result = self.asr.load_model(model)
        except ASRError as exc:
            status = getattr(exc, "status", 0)
            if status in (400, 409):
                raise InvalidState(str(exc)) from exc
            raise UpstreamError(f"切换模型失败：{exc}") from exc
        self._audit("", "asr_model_switched", {"model": model, "elapsed": result.get("elapsed")})
        return {
            "model": result.get("model") or model,
            "previous": result.get("previous", ""),
            "elapsed": result.get("elapsed", 0),
            "changed": bool(result.get("changed", True)),
            "source": result.get("source", ""),
        }

    def health(self) -> dict:
        components = []

        try:
            asr_health = self.asr.health()
            components.append({"name": "asr", "url": self.cfg.asr_url, "ok": True,
                               "detail": asr_health, "error": ""})
        except ASRError as exc:
            components.append({"name": "asr", "url": self.cfg.asr_url, "ok": False,
                               "detail": {}, "error": str(exc)})

        try:
            engine_health = self.engine.health()
            components.append({"name": "engine", "url": self.cfg.engine_url, "ok": True,
                               "detail": engine_health, "error": ""})
        except EngineError as exc:
            components.append({"name": "engine", "url": self.cfg.engine_url, "ok": False,
                               "detail": {}, "error": str(exc)})

        return {
            "ok": all(c["ok"] for c in components),
            "terms": {"version": self.corrector.version, "entries": len(self.corrector.entries)},
            "ffmpeg": self.cfg.ffmpeg,
            "thresholds": {
                "min_duration_ms": self.cfg.min_duration_ms,
                "max_duration_ms": self.cfg.max_duration_ms,
                "low_confidence": self.cfg.low_confidence_threshold,
                "silence_rms": self.cfg.silence_rms,
            },
            "components": components,
        }
