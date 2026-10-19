"""Thống kê và báo cáo (chỉ đọc). Không tạo bảng mới: mọi số liệu tính trực tiếp từ 4 bảng chính.

Quy ước:
- Mọi người dùng đang hoạt động đều xem được toàn bộ số liệu (STAFF xem tất cả công việc của phòng).
- "Quá hạn" = công việc chưa báo cáo (Bản nháp, Chờ phê duyệt, Bị từ chối, Đang thực hiện)
  có hạn hoàn thành trước hôm nay (giờ Việt Nam), cùng định nghĩa với task_service.is_overdue.
- Không cộng khối lượng giữa các phần việc vì đơn vị khác nhau (trạm, báo cáo, giờ...).
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config
from core import database
from core.timeutil import today_vn

# Công việc đã được duyệt và thực sự triển khai (dùng cho thống kê người thực hiện)
_EXECUTED_STATUSES = (
    config.STATUS_IN_PROGRESS,
    config.STATUS_REPORTED,
    config.STATUS_PASSED,
    config.STATUS_FAILED,
)

PERIODS = ("Tất cả thời gian", "Tháng này", "Tháng trước", "Quý này", "Năm nay", "Tùy chọn")


def _in(values: Sequence[str]) -> str:
    return ", ".join("?" for _ in values)


# --- Khoảng thời gian ----------------------------------------------------------
def period_range(name: str, today: Optional[date] = None) -> Tuple[Optional[date], Optional[date]]:
    """Khoảng ngày (từ, đến) cho một lựa chọn kỳ báo cáo. (None, None) nghĩa là không giới hạn."""
    today = today or today_vn()
    if name == "Tháng này":
        start = today.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return start, end
    if name == "Tháng trước":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if name == "Quý này":
        first_month = 3 * ((today.month - 1) // 3) + 1
        start = date(today.year, first_month, 1)
        next_start = date(today.year + (first_month + 3 > 12), (first_month + 3 - 1) % 12 + 1, 1)
        return start, next_start - timedelta(days=1)
    if name == "Năm nay":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    return None, None


def _date_filter(column: str, date_from: Optional[date], date_to: Optional[date]) -> Tuple[List[str], List[Any]]:
    where: List[str] = []
    params: List[Any] = []
    if date_from is not None:
        where.append(f"{column} >= ?")
        params.append(date_from.isoformat())
    if date_to is not None:
        where.append(f"{column} <= ?")
        params.append(date_to.isoformat())
    return where, params


# --- Tổng quan -------------------------------------------------------------------
def overview(due_soon_days: int = 7, today: Optional[date] = None) -> Dict[str, Any]:
    """Số liệu cho trang Tổng quan."""
    today = today or today_vn()
    today_iso = today.isoformat()
    soon_iso = (today + timedelta(days=due_soon_days)).isoformat()
    open_statuses = config.OPEN_STATUSES

    with database.connect() as conn:
        counts = {s: 0 for s in config.STATUSES}
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
            counts[row["status"]] = row["n"]

        base = (
            "SELECT t.id, t.title, t.task_type, t.status, t.deadline, l.full_name AS lead_name, "
            "p.full_name AS proposer_name, "
            "(SELECT AVG(m.progress) FROM task_members m WHERE m.task_id = t.id) AS avg_progress "
            "FROM tasks t JOIN users p ON p.id = t.proposer_id LEFT JOIN users l ON l.id = t.lead_id "
        )
        overdue = [
            dict(r)
            for r in conn.execute(
                base + f"WHERE t.status IN ({_in(open_statuses)}) AND t.deadline < ? "
                "ORDER BY t.deadline, t.id",
                (*open_statuses, today_iso),
            )
        ]
        due_soon = [
            dict(r)
            for r in conn.execute(
                base + "WHERE t.status = ? AND t.deadline >= ? AND t.deadline <= ? "
                "ORDER BY t.deadline, t.id",
                (config.STATUS_IN_PROGRESS, today_iso, soon_iso),
            )
        ]
        avg_row = conn.execute(
            "SELECT AVG(m.progress) FROM task_members m JOIN tasks t ON t.id = m.task_id WHERE t.status = ?",
            (config.STATUS_IN_PROGRESS,),
        ).fetchone()

        workload = [
            dict(r)
            for r in conn.execute(
                "SELECT u.id AS user_id, u.full_name, "
                "COUNT(CASE WHEN t.status = ? THEN 1 END) AS active_parts, "
                "COUNT(CASE WHEN t.status = ? AND m.progress >= 100 THEN 1 END) AS done_parts, "
                "AVG(CASE WHEN t.status = ? THEN m.progress END) AS avg_progress, "
                "COUNT(CASE WHEN t.status = ? AND t.deadline < ? THEN 1 END) AS overdue_parts "
                "FROM users u "
                "LEFT JOIN task_members m ON m.user_id = u.id "
                "LEFT JOIN tasks t ON t.id = m.task_id "
                f"WHERE u.is_active = 1 AND u.role IN ({_in(config.ASSIGNABLE_ROLES)}) "
                "GROUP BY u.id, u.full_name ORDER BY u.full_name COLLATE NOCASE, u.id",
                (
                    config.STATUS_IN_PROGRESS,
                    config.STATUS_IN_PROGRESS,
                    config.STATUS_IN_PROGRESS,
                    config.STATUS_IN_PROGRESS,
                    today_iso,
                    *config.ASSIGNABLE_ROLES,
                ),
            )
        ]
        leading = {
            r["lead_id"]: r["n"]
            for r in conn.execute(
                "SELECT lead_id, COUNT(*) AS n FROM tasks WHERE status = ? AND lead_id IS NOT NULL GROUP BY lead_id",
                (config.STATUS_IN_PROGRESS,),
            )
        }
    for item in workload:
        item["leading"] = leading.get(item["user_id"], 0)

    return {
        "status_counts": counts,
        "total": sum(counts.values()),
        "pending_approvals": counts[config.STATUS_PENDING_APPROVAL],
        "pending_evaluations": counts[config.STATUS_REPORTED],
        "in_progress": counts[config.STATUS_IN_PROGRESS],
        "passed": counts[config.STATUS_PASSED],
        "failed": counts[config.STATUS_FAILED],
        "overdue": overdue,
        "overdue_count": len(overdue),
        "due_soon": due_soon,
        "avg_progress_in_progress": avg_row[0],
        "workload": workload,
    }


# --- Báo cáo công việc ------------------------------------------------------------
def task_report(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    statuses: Optional[Sequence[str]] = None,
    task_type: Optional[str] = None,
    user_id: Optional[int] = None,
    today: Optional[date] = None,
) -> List[dict]:
    """Danh sách công việc cho báo cáo, lọc theo hạn hoàn thành trong khoảng [từ, đến]."""
    today_iso = (today or today_vn()).isoformat()
    where, params = _date_filter("t.deadline", date_from, date_to)
    if statuses:
        where.append(f"t.status IN ({_in(statuses)})")
        params.extend(statuses)
    if task_type:
        where.append("t.task_type = ?")
        params.append(task_type)
    if user_id is not None:
        where.append(
            "(t.proposer_id = ? OR t.lead_id = ? OR EXISTS "
            "(SELECT 1 FROM task_members m WHERE m.task_id = t.id AND m.user_id = ?))"
        )
        params.extend([user_id] * 3)
    sql = (
        "SELECT t.id, t.title, t.task_type, t.status, t.deadline, t.completed_at, t.eval_result, "
        "t.eval_comment, p.full_name AS proposer_name, l.full_name AS lead_name, "
        "(SELECT COUNT(*) FROM task_members m WHERE m.task_id = t.id) AS member_count, "
        "(SELECT AVG(m.progress) FROM task_members m WHERE m.task_id = t.id) AS avg_progress "
        "FROM tasks t JOIN users p ON p.id = t.proposer_id LEFT JOIN users l ON l.id = t.lead_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.deadline, t.id"
    with database.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    for row in rows:
        row["overdue"] = row["status"] in config.OPEN_STATUSES and row["deadline"] < today_iso
    return rows


def member_report(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    today: Optional[date] = None,
) -> List[dict]:
    """Thống kê theo người, trên các công việc đã triển khai có hạn trong khoảng [từ, đến].

    - parts: số phần việc được giao; completed: số phần đạt 100%; avg_progress: tiến độ TB các phần
    - passed / failed: số công việc có phần việc của người đó được đánh giá Đạt / Không đạt
    - led_total / led_passed / led_failed: các công việc người đó chủ trì
    - overdue_parts: phần việc chưa xong của công việc đang thực hiện đã quá hạn
    """
    today_iso = (today or today_vn()).isoformat()
    date_where, date_params = _date_filter("t.deadline", date_from, date_to)
    extra = (" AND " + " AND ".join(date_where)) if date_where else ""

    with database.connect() as conn:
        people = conn.execute(
            f"SELECT id, full_name FROM users WHERE is_active = 1 AND role IN ({_in(config.ASSIGNABLE_ROLES)}) "
            "ORDER BY full_name COLLATE NOCASE, id",
            tuple(config.ASSIGNABLE_ROLES),
        ).fetchall()
        parts = {
            r["user_id"]: dict(r)
            for r in conn.execute(
                "SELECT m.user_id, COUNT(*) AS parts, "
                "COUNT(CASE WHEN m.progress >= 100 THEN 1 END) AS completed, "
                "AVG(m.progress) AS avg_progress, "
                "COUNT(CASE WHEN t.status = ? THEN 1 END) AS passed, "
                "COUNT(CASE WHEN t.status = ? THEN 1 END) AS failed, "
                "COUNT(CASE WHEN t.status = ? AND m.progress < 100 AND t.deadline < ? THEN 1 END) AS overdue_parts "
                "FROM task_members m JOIN tasks t ON t.id = m.task_id "
                f"WHERE t.status IN ({_in(_EXECUTED_STATUSES)}){extra} GROUP BY m.user_id",
                (
                    config.STATUS_PASSED,
                    config.STATUS_FAILED,
                    config.STATUS_IN_PROGRESS,
                    today_iso,
                    *_EXECUTED_STATUSES,
                    *date_params,
                ),
            )
        }
        led = {
            r["lead_id"]: dict(r)
            for r in conn.execute(
                "SELECT t.lead_id, COUNT(*) AS led_total, "
                "COUNT(CASE WHEN t.status = ? THEN 1 END) AS led_passed, "
                "COUNT(CASE WHEN t.status = ? THEN 1 END) AS led_failed "
                f"FROM tasks t WHERE t.lead_id IS NOT NULL AND t.status IN ({_in(_EXECUTED_STATUSES)}){extra} "
                "GROUP BY t.lead_id",
                (config.STATUS_PASSED, config.STATUS_FAILED, *_EXECUTED_STATUSES, *date_params),
            )
        }
    result = []
    for person in people:
        p = parts.get(person["id"], {})
        l = led.get(person["id"], {})
        result.append(
            {
                "user_id": person["id"],
                "full_name": person["full_name"],
                "parts": p.get("parts", 0),
                "completed": p.get("completed", 0),
                "avg_progress": p.get("avg_progress"),
                "passed": p.get("passed", 0),
                "failed": p.get("failed", 0),
                "overdue_parts": p.get("overdue_parts", 0),
                "led_total": l.get("led_total", 0),
                "led_passed": l.get("led_passed", 0),
                "led_failed": l.get("led_failed", 0),
            }
        )
    return result


def outcome_summary(rows: Sequence[dict]) -> Dict[str, Any]:
    """Tóm tắt nhanh một danh sách công việc của task_report."""
    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == config.STATUS_PASSED)
    failed = sum(1 for r in rows if r["status"] == config.STATUS_FAILED)
    evaluated = passed + failed
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "overdue": sum(1 for r in rows if r["overdue"]),
        "pass_rate": (passed / evaluated) if evaluated else None,
    }


# --- Xuất CSV ----------------------------------------------------------------------
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> Any:
    """Chặn chèn công thức khi mở bằng Excel (CSV injection): ô văn bản bắt đầu bằng = + - @ bị thêm dấu '."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv(columns: Sequence[Tuple[str, str]], rows: Sequence[dict]) -> bytes:
    """CSV UTF-8 có BOM (Excel đọc đúng tiếng Việt). `columns`: danh sách (khóa, tiêu đề cột)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_csv_cell(title) for _, title in columns])
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key, _ in columns])
    return buffer.getvalue().encode("utf-8-sig")
