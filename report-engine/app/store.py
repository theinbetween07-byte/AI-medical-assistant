"""本地存档与审计（FR-10 / BR-12）。

SQLite 单文件，零运维（技术设计 §2）。报告以 JSON 快照存储，
保证历史会话可重放、可追溯。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .schemas import AuditEvent, GeneratedReport

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id           TEXT PRIMARY KEY,
    session_id   TEXT,
    template_id  TEXT,
    status       TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    event_type TEXT,
    payload    TEXT,
    at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_session ON reports(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit(session_id);
"""


class Store:
    def __init__(self, db_path: Path | str | None) -> None:
        target = ":memory:" if db_path in (None, "") else str(db_path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ 报告
    def save_report(self, report: GeneratedReport) -> None:
        payload = report.model_dump_json()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO reports (id, session_id, template_id, status, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id=excluded.session_id,
                    template_id=excluded.template_id,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    payload=excluded.payload
                """,
                (
                    report.id,
                    report.session_id,
                    report.template_id,
                    report.status,
                    report.created_at,
                    report.created_at,
                    payload,
                ),
            )
            self._conn.commit()

    def get_report(self, report_id: str) -> GeneratedReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        return GeneratedReport.model_validate_json(row["payload"])

    def list_reports(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, template_id, status, created_at FROM reports "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_report(self, report_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ 审计
    def log(self, event: AuditEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit (session_id, event_type, payload, at) VALUES (?, ?, ?, ?)",
                (event.session_id, event.event_type, json.dumps(event.payload, ensure_ascii=False), event.at),
            )
            self._conn.commit()

    def list_audit(self, session_id: str = "", limit: int = 200) -> list[dict]:
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            out.append(item)
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
