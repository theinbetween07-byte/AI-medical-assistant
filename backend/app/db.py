"""
SQLite 數據訪問層

- 用戶表 users：註冊、登錄
- ASR 日誌表 asr_logs：審計用，記錄誰提交了什麼音頻、用了什麼模型
"""

import os
import sqlite3
import threading
from typing import Optional

from app.config import DATABASE_PATH


_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")  # 並發友好
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    """啟動時初始化表結構"""
    with _lock, _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'doctor',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS asr_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            template TEXT,
            language TEXT,
            model_size TEXT,
            audio_duration REAL,
            elapsed REAL,
            text_length INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_asr_logs_user
            ON asr_logs(user_id, created_at DESC);
        """)


# ============ Users ============
def create_user(username: str, display_name: str, password_hash: str, role: str = "doctor") -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
            (username, display_name, password_hash, role),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def update_last_login(user_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )


def list_users() -> list[dict]:
    """管理員用：列出所有用戶"""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, role, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


# ============ ASR Logs ============
def log_asr(
    user_id: int,
    username: str,
    template: str,
    language: str,
    model_size: str,
    audio_duration: float,
    elapsed: float,
    text_length: int,
) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO asr_logs
               (user_id, username, template, language, model_size,
                audio_duration, elapsed, text_length)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, template, language, model_size,
             audio_duration, elapsed, text_length),
        )


def get_user_asr_logs(user_id: int, limit: int = 50) -> list[dict]:
    """取最近 N 條轉寫記錄（個人中心用）"""
    with _lock, _connect() as conn:
        rows = conn.execute(
            """SELECT id, template, language, model_size, audio_duration,
                      elapsed, text_length, created_at
               FROM asr_logs
               WHERE user_id = ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
