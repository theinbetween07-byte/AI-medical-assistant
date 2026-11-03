"""会话列表的搜索与筛选（纯函数，便于单测）。

只做「取数后的过滤」，不碰数据库：`store.list_sessions_for_search()` 负责 join 报告，
这里负责关键词匹配、状态归类与时间窗。会话量级很小（单机单医生），Python 侧过滤足够，
也避免依赖 SQLite 的 JSON 扩展。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

# 报告状态归并：医生只关心「签没签」
_SIGNED = {"signed"}
_SEARCH_FIELDS = ("title", "id", "report_id")


def _report_status(row: dict) -> str:
    payload = row.get("report_payload")
    if not payload:
        return "none"
    try:
        status = str((json.loads(payload) or {}).get("status") or "").strip().lower()
    except (ValueError, AttributeError):
        return "draft"
    return "signed" if status in _SIGNED else "draft"


def _cutoff(days: int, now: datetime | None = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base - timedelta(days=days)).isoformat()


def _haystack(row: dict) -> str:
    parts = [str(row.get(field) or "") for field in _SEARCH_FIELDS]
    # 报告正文一起搜：患者名、主诉、诊断都只在 payload 里
    parts.append(str(row.get("report_payload") or ""))
    return "\n".join(parts).lower()


def filter_sessions(
    rows: list[dict],
    q: str = "",
    status: str = "",
    days: int = 0,
    limit: int = 0,
    now: datetime | None = None,
) -> list[dict]:
    """按关键词 / 报告状态 / 时间窗过滤，返回带 `report_status` 的新列表。

    - `q`：不区分大小写，匹配标题、会话号、报告号与报告全文；
    - `status`：`signed` | `draft` | `none`（空表示全部）；
    - `days`：只看最近 N 天内更新过的会话（0 表示不限）。
    """
    keyword = (q or "").strip().lower()
    want = (status or "").strip().lower()
    cutoff = _cutoff(days, now) if days > 0 else ""

    out: list[dict] = []
    for row in rows:
        item = dict(row)
        item["report_status"] = _report_status(row)
        item.pop("report_payload", None)
        if want and item["report_status"] != want:
            continue
        if cutoff and str(item.get("updated_at") or item.get("created_at") or "") < cutoff:
            continue
        if keyword and keyword not in _haystack(row):
            continue
        out.append(item)

    out.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
    return out[:limit] if limit > 0 else out
