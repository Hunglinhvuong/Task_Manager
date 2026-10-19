"""Nhật ký thao tác (task_logs). Chỉ có thêm và đọc, không có sửa/xóa."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, List, Optional

from core import database
from core.timeutil import now_iso


def add_log(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    action: str,
    detail: Optional[Any] = None,
    member_id: Optional[int] = None,
) -> int:
    """Ghi một dòng nhật ký bằng chính connection/transaction của thao tác đang làm.

    Nhờ vậy thay đổi nghiệp vụ và nhật ký cùng thành công hoặc cùng bị hủy.
    """
    cur = conn.execute(
        "INSERT INTO task_logs (task_id, member_id, actor_id, action, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            task_id,
            member_id,
            actor_id,
            action,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None,
            now_iso(),
        ),
    )
    return int(cur.lastrowid)


def get_logs(task_id: int) -> List[dict]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT l.id, l.task_id, l.member_id, l.actor_id, u.full_name AS actor_name, "
            "l.action, l.detail, l.created_at "
            "FROM task_logs l JOIN users u ON u.id = l.actor_id "
            "WHERE l.task_id = ? ORDER BY l.id",
            (task_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else None
        result.append(item)
    return result
