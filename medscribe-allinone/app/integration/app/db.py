"""SQLite 存储层（FR-10 / BR-12）。

只存"会话级"数据：会话、分段、要素、报告快照、导出记录、审计事件。
报告的权威副本仍在报告引擎里，这里存快照是为了让历史会话在引擎数据被清理后
仍能回放（AC-10）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT DEFAULT '',
    audio_filename TEXT DEFAULT '',
    audio_path TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    language TEXT DEFAULT '',
    language_probability REAL DEFAULT 0,
    template_id TEXT DEFAULT '',
    report_id TEXT,
    segment_count INTEGER DEFAULT 0,
    warnings TEXT DEFAULT '[]',
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    session_id TEXT NOT NULL,
    id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start_ms INTEGER DEFAULT 0,
    end_ms INTEGER DEFAULT 0,
    text TEXT DEFAULT '',
    text_raw TEXT,
    lang TEXT DEFAULT 'unknown',
    confidence REAL DEFAULT 1.0,
    confidence_source TEXT DEFAULT 'asr-global',
    low_confidence INTEGER DEFAULT 0,
    confirmed INTEGER DEFAULT 0,
    edited INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, id)
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id, idx);

CREATE TABLE IF NOT EXISTS elements (
    session_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    session_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    session_id TEXT PRIMARY KEY,
    report_id TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    report_id TEXT,
    format TEXT NOT NULL,
    path TEXT DEFAULT '',
    local_path TEXT DEFAULT '',
    exported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exports_session ON exports(session_id);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit(session_id, id);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """为旧库补列/换主键（纯增量，不动已有数据）。"""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(exports)")}
        if "local_path" not in cols:
            self._conn.execute("ALTER TABLE exports ADD COLUMN local_path TEXT DEFAULT ''")

        # 早期版本把 segments.id 当全局主键，导致不同会话的 S-001 冲突。
        # 现在改为 (session_id, id) 复合主键。
        info = list(self._conn.execute("PRAGMA table_info(segments)"))
        pk_cols = [row["name"] for row in info if row["pk"]]
        if pk_cols == ["id"]:
            self._conn.executescript(
                """
                ALTER TABLE segments RENAME TO segments_legacy;
                CREATE TABLE segments (
                    session_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    start_ms INTEGER DEFAULT 0,
                    end_ms INTEGER DEFAULT 0,
                    text TEXT DEFAULT '',
                    text_raw TEXT,
                    lang TEXT DEFAULT 'unknown',
                    confidence REAL DEFAULT 1.0,
                    confidence_source TEXT DEFAULT 'asr-global',
                    low_confidence INTEGER DEFAULT 0,
                    confirmed INTEGER DEFAULT 0,
                    edited INTEGER DEFAULT 0,
                    PRIMARY KEY (session_id, id)
                );
                INSERT OR REPLACE INTO segments
                    SELECT session_id, id, idx, start_ms, end_ms, text, text_raw, lang,
                           confidence, confidence_source, low_confidence, confirmed, edited
                    FROM segments_legacy;
                DROP TABLE segments_legacy;
                CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id, idx);
                """
            )

    # ---------------------------------------------------------------- helpers
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # --------------------------------------------------------------- sessions
    def upsert_session(self, data: dict) -> None:
        payload = dict(data)
        for key in ("warnings",):
            if not isinstance(payload.get(key), str):
                payload[key] = json.dumps(payload.get(key) or [], ensure_ascii=False)
        cols = [
            "id", "created_at", "updated_at", "status", "title", "audio_filename",
            "audio_path", "duration_ms", "language", "language_probability",
            "template_id", "report_id", "segment_count", "warnings", "deleted_at",
        ]
        values = [payload.get(c) for c in cols]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in cols if c != "id")
        self._exec(
            f"INSERT INTO sessions ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )

    def get_session(self, session_id: str) -> Optional[dict]:
        rows = self._query("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(rows[0]) if rows else None

    def list_sessions(self, limit: int = 50, include_deleted: bool = False) -> list[dict]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        rows = self._query(
            f"SELECT * FROM sessions {where} ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def list_sessions_for_search(self, limit: int = 500) -> list[dict]:
        """带报告正文的会话列表，供 `app/history.py` 做关键词搜索。"""
        rows = self._query(
            "SELECT s.*, IFNULL(r.payload, '') AS report_payload"
            " FROM sessions s LEFT JOIN reports r ON r.session_id = s.id"
            " WHERE s.deleted_at IS NULL"
            " ORDER BY s.updated_at DESC, s.created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def mark_deleted(self, session_id: str, at: str) -> None:
        self._exec(
            "UPDATE sessions SET deleted_at = ?, status = 'deleted' WHERE id = ?",
            (at, session_id),
        )

    def purge_session(self, session_id: str) -> None:
        """级联删除（BR-12：音频由调用方物理删除）。"""
        for table in ("segments", "elements", "corrections", "reports", "exports"):
            self._exec(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))

    # --------------------------------------------------------------- segments
    def replace_segments(self, session_id: str, segments: list[dict]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
            self._conn.executemany(
                "INSERT INTO segments (session_id, id, idx, start_ms, end_ms, text, text_raw,"
                " lang, confidence, confidence_source, low_confidence, confirmed, edited)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        session_id, s["id"], s.get("index", i), s.get("start_ms", 0),
                        s.get("end_ms", 0), s.get("text", ""), s.get("text_raw"),
                        s.get("lang", "unknown"), s.get("confidence", 1.0),
                        s.get("confidence_source", "asr-global"),
                        int(bool(s.get("low_confidence"))), int(bool(s.get("confirmed"))),
                        int(bool(s.get("edited"))),
                    )
                    for i, s in enumerate(segments)
                ],
            )
            self._conn.commit()

    def list_segments(self, session_id: str) -> list[dict]:
        rows = self._query(
            "SELECT * FROM segments WHERE session_id = ? ORDER BY idx", (session_id,)
        )
        out = []
        for r in rows:
            d = dict(r)
            d["low_confidence"] = bool(d["low_confidence"])
            d["confirmed"] = bool(d["confirmed"])
            d["edited"] = bool(d["edited"])
            out.append(d)
        return out

    def get_segment(self, session_id: str, segment_id: str) -> Optional[dict]:
        rows = self._query(
            "SELECT * FROM segments WHERE session_id = ? AND id = ?", (session_id, segment_id)
        )
        if not rows:
            return None
        d = dict(rows[0])
        for k in ("low_confidence", "confirmed", "edited"):
            d[k] = bool(d[k])
        return d

    def update_segment(self, session_id: str, segment_id: str, fields: dict) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(
            f"UPDATE segments SET {cols} WHERE session_id = ? AND id = ?",
            [*fields.values(), session_id, segment_id],
        )

    # ---------------------------------------------------- elements/corrections
    def save_elements(self, session_id: str, elements: list[dict], at: str) -> None:
        self._exec(
            "INSERT INTO elements (session_id, payload, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload,"
            " updated_at=excluded.updated_at",
            (session_id, json.dumps(elements, ensure_ascii=False), at),
        )

    def load_elements(self, session_id: str) -> list[dict]:
        rows = self._query("SELECT payload FROM elements WHERE session_id = ?", (session_id,))
        return json.loads(rows[0]["payload"]) if rows else []

    def save_corrections(self, session_id: str, corrections: list[dict], at: str) -> None:
        self._exec(
            "INSERT INTO corrections (session_id, payload, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload,"
            " updated_at=excluded.updated_at",
            (session_id, json.dumps(corrections, ensure_ascii=False), at),
        )

    def load_corrections(self, session_id: str) -> list[dict]:
        rows = self._query(
            "SELECT payload FROM corrections WHERE session_id = ?", (session_id,)
        )
        return json.loads(rows[0]["payload"]) if rows else []

    # ----------------------------------------------------------------- report
    def save_report(self, session_id: str, report: dict, at: str) -> None:
        self._exec(
            "INSERT INTO reports (session_id, report_id, payload, updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET"
            " report_id=excluded.report_id, payload=excluded.payload,"
            " updated_at=excluded.updated_at",
            (session_id, report.get("id"), json.dumps(report, ensure_ascii=False), at),
        )

    def load_report(self, session_id: str) -> Optional[dict]:
        rows = self._query(
            "SELECT payload FROM reports WHERE session_id = ? ORDER BY updated_at DESC",
            (session_id,),
        )
        return json.loads(rows[0]["payload"]) if rows else None

    # ---------------------------------------------------------------- exports
    def add_export(self, session_id: str, record: dict, at: str) -> None:
        self._exec(
            "INSERT INTO exports (session_id, report_id, format, path, local_path, exported_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                session_id,
                record.get("report_id"),
                record.get("format", ""),
                record.get("path", ""),
                record.get("local_path", ""),
                record.get("exported_at", at),
            ),
        )

    def list_exports(self, session_id: str) -> list[dict]:
        rows = self._query(
            "SELECT * FROM exports WHERE session_id = ? ORDER BY id", (session_id,)
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ audit
    def add_audit(self, session_id: str, event_type: str, payload: dict, at: str) -> None:
        self._exec(
            "INSERT INTO audit (session_id, event_type, payload, at) VALUES (?,?,?,?)",
            (session_id, event_type, json.dumps(payload, ensure_ascii=False), at),
        )

    def list_audit(self, session_id: str = "", limit: int = 200) -> list[dict]:
        if session_id:
            rows = self._query(
                "SELECT * FROM audit WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        else:
            rows = self._query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            out.append(d)
        return out
