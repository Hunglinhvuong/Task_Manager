"""Nghiệp vụ công việc: tạo, sửa, gửi/duyệt/từ chối, phân công phần việc, truy vấn.

Quy ước:
- Mọi thao tác ghi chạy trong một transaction, đọc lại công việc ngay trong
  transaction rồi mới kiểm tra trạng thái và quyền, và ghi task_logs cùng
  transaction đó (cùng thành công hoặc cùng bị hủy).
- Lỗi dữ liệu / sai trạng thái: ValueError. Không đủ quyền: PermissionDenied.
- Tiến độ và đánh giá kết quả thuộc Giai đoạn 3.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

import config
from core import database, permissions
from core.log_service import add_log
from core.timeutil import now_iso, today_vn

_ALLOCATION_STATUSES = (
    config.STATUS_DRAFT,
    config.STATUS_REJECTED,
    config.STATUS_IN_PROGRESS,
)
# Chỉ các cột này được phép sửa qua update_task (dùng để dựng câu UPDATE).
_EDITABLE_COLUMNS = ("title", "description", "deadline", "lead_id")


def has_quantity(quantity: Any) -> bool:
    """Phần việc có khối lượng giao (> 0) thì tiến độ được tính tự động từ khối lượng đã hoàn thành."""
    return quantity is not None and quantity > 0


def compute_progress(done_quantity: Any, quantity: Any) -> int:
    """Tiến độ (%) = khối lượng đã hoàn thành / khối lượng được giao, làm tròn về số nguyên 0-100.

    Chỉ đạt 100% khi đã làm đủ (hoặc vượt) khối lượng giao; trước đó tối đa là 99% để
    làm tròn không báo hoàn thành sớm (ví dụ 996/1000 hiển thị 99%, không phải 100%).
    """
    if not has_quantity(quantity):
        raise ValueError("Phần việc chưa có khối lượng giao nên không tự tính được tiến độ.")
    done = float(done_quantity or 0)
    if done >= quantity:
        return 100
    return max(0, min(99, round(done / quantity * 100)))


def _status_label(code: str) -> str:
    return config.STATUS_LABELS.get(code, code)


# --- Kiểm tra / chuẩn hóa dữ liệu --------------------------------------
def _clean_text(value: Optional[str], label: str, *, required: bool, max_len: int) -> Optional[str]:
    text = (value or "").strip()
    if required and not text:
        raise ValueError(f"{label} không được để trống.")
    if len(text) > max_len:
        raise ValueError(f"{label} tối đa {max_len} ký tự.")
    return text if required else (text or None)


def _parse_deadline(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError("Hạn hoàn thành không hợp lệ (định dạng YYYY-MM-DD).") from None


def _check_not_past(deadline_iso: str) -> None:
    if deadline_iso < today_vn().isoformat():
        raise ValueError("Hạn hoàn thành không được ở quá khứ.")


def _normalize_quantity(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Khối lượng phải là một số.") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError("Khối lượng phải là số không âm.")
    return number


def _normalize_part(content: Any, quantity: Any, unit: Any):
    clean_content = _clean_text(content, "Nội dung phần việc", required=True, max_len=config.MAX_PART_LENGTH)
    clean_unit = _clean_text(unit, "Đơn vị", required=False, max_len=config.MAX_UNIT_LENGTH)
    return clean_content, _normalize_quantity(quantity), clean_unit


# --- Truy cập dữ liệu trong transaction --------------------------------
def _task_row(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy công việc.")
    return row


def _expect_status(task, allowed: Sequence[str], message: str) -> None:
    if task["status"] not in allowed:
        raise ValueError(message)


def _require_assignable(conn: sqlite3.Connection, user_id: Any, label: str) -> sqlite3.Row:
    row = None
    if user_id is not None:
        row = conn.execute(
            "SELECT id, full_name, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if row is None or not row["is_active"] or row["role"] not in config.ASSIGNABLE_ROLES:
        raise ValueError(
            f"{label} không hợp lệ (tài khoản không tồn tại, đã bị khóa hoặc không được giao việc)."
        )
    return row


def _touch_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_iso(), task_id))


def _insert_member(
    conn: sqlite3.Connection,
    task_id: int,
    actor_id: int,
    user_id: Any,
    content: Any,
    quantity: Any,
    unit: Any,
) -> int:
    member = _require_assignable(conn, user_id, "Thành viên")
    clean_content, clean_quantity, clean_unit = _normalize_part(content, quantity, unit)
    exists = conn.execute(
        "SELECT 1 FROM task_members WHERE task_id = ? AND user_id = ?", (task_id, member["id"])
    ).fetchone()
    if exists:
        raise ValueError(f"{member['full_name']} đã có phần việc trong công việc này (mỗi người chỉ có một phần việc).")
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO task_members (task_id, user_id, content, quantity, unit, done_quantity, progress, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (
            task_id,
            member["id"],
            clean_content,
            clean_quantity,
            clean_unit,
            0.0 if has_quantity(clean_quantity) else None,
            now,
            now,
        ),
    )
    member_id = int(cur.lastrowid)
    add_log(
        conn,
        task_id,
        actor_id,
        config.LOG_MEMBER_ADDED,
        {
            "user_id": member["id"],
            "user_name": member["full_name"],
            "content": clean_content,
            "quantity": clean_quantity,
            "unit": clean_unit,
        },
        member_id=member_id,
    )
    return member_id


# --- Tạo / sửa công việc ------------------------------------------------
def create_task(
    actor: dict,
    task_type: str,
    title: str,
    description: Optional[str],
    deadline: Any,
    lead_id: Optional[int] = None,
    members: Optional[Sequence[dict]] = None,
) -> int:
    """Tạo công việc.

    - DIRECT (chỉ trưởng phòng): giao trực tiếp cho ĐÚNG MỘT người, bỏ qua bước phê duyệt, vào thẳng
      "Đang thực hiện". Người được giao là người chủ trì (`lead_id`) và có đúng một phần việc.
      `members` gồm đúng một phần việc {content, quantity, unit}; `user_id` có thể bỏ trống (lấy
      `lead_id`), nếu có thì phải trùng `lead_id`.
    - TEAM (trưởng phòng hoặc nhân viên): tạo ở trạng thái "Bản nháp", cần chủ trì.
    - PERSONAL (nhân viên): tự phân công đúng một phần việc cho mình, gửi duyệt ngay.
      Sau khi trưởng phòng duyệt, hoạt động như DIRECT (tự chuyển chờ đánh giá khi hoàn tất).
    `members`: danh sách dict {user_id, content, quantity, unit}.
    """
    permissions.require(
        permissions.can_create_task(actor, task_type), "Bạn không có quyền tạo loại công việc này."
    )
    clean_title = _clean_text(title, "Tên công việc", required=True, max_len=config.MAX_TITLE_LENGTH)
    clean_description = _clean_text(
        description, "Mô tả", required=False, max_len=config.MAX_TEXT_LENGTH
    )
    deadline_iso = _parse_deadline(deadline)
    _check_not_past(deadline_iso)

    member_list = [dict(m) for m in (members or [])]
    if task_type == config.TYPE_DIRECT:
        if lead_id is None:
            raise ValueError("Cần chọn người được giao việc.")
        if len(member_list) != 1:
            raise ValueError("Công việc giao trực tiếp chỉ giao cho một người và có đúng một phần việc.")
        if member_list[0].get("user_id") not in (None, lead_id):
            raise ValueError("Công việc giao trực tiếp chỉ giao cho một người: người được giao là người chủ trì.")
        member_list[0]["user_id"] = lead_id
    elif task_type == config.TYPE_PERSONAL:
        if lead_id != actor["id"]:
            raise ValueError("Công việc cá nhân phải do chính người tạo thực hiện.")
        if len(member_list) != 1:
            raise ValueError("Công việc cá nhân phải có đúng một phần việc của người tạo.")
        if member_list[0].get("user_id") not in (None, actor["id"]):
            raise ValueError("Công việc cá nhân chỉ được giao cho chính người tạo.")
        member_list[0]["user_id"] = actor["id"]
    user_ids = [m.get("user_id") for m in member_list]
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("Mỗi người chỉ có một phần việc trong một công việc.")

    if task_type == config.TYPE_DIRECT:
        status = config.STATUS_IN_PROGRESS
    elif task_type == config.TYPE_PERSONAL:
        status = config.STATUS_PENDING_APPROVAL
    else:
        if lead_id is None:
            raise ValueError("Cần chọn người chủ trì.")
        status = config.STATUS_DRAFT

    now = now_iso()
    with database.transaction() as conn:
        lead_name = None
        if lead_id is not None:
            lead_name = _require_assignable(conn, lead_id, "Người chủ trì")["full_name"]
        is_direct = task_type == config.TYPE_DIRECT
        is_personal = task_type == config.TYPE_PERSONAL
        cur = conn.execute(
            "INSERT INTO tasks (title, description, task_type, status, proposer_id, lead_id, "
            "approver_id, deadline, submitted_at, approved_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clean_title,
                clean_description,
                task_type,
                status,
                actor["id"],
                lead_id,
                actor["id"] if is_direct else None,
                deadline_iso,
                now if is_personal else None,
                now if is_direct else None,
                now,
                now,
            ),
        )
        task_id = int(cur.lastrowid)
        add_log(
            conn,
            task_id,
            actor["id"],
            config.LOG_TASK_CREATED,
            {
                "task_type": task_type,
                "title": clean_title,
                "deadline": deadline_iso,
                "lead": lead_name,
            },
        )
        for item in member_list:
            _insert_member(
                conn,
                task_id,
                actor["id"],
                item.get("user_id"),
                item.get("content"),
                item.get("quantity"),
                item.get("unit"),
            )
        if is_direct:
            add_log(conn, task_id, actor["id"], config.LOG_APPROVED, {"auto": True})
        elif is_personal:
            add_log(conn, task_id, actor["id"], config.LOG_SUBMITTED, {"from": config.STATUS_DRAFT})
        return task_id


def update_task(
    actor: dict,
    task_id: int,
    *,
    title: str,
    description: Optional[str],
    deadline: Any,
    lead_id: Optional[int] = None,
) -> bool:
    """Sửa thông tin công việc. Trả về False nếu không có gì thay đổi (không ghi nhật ký)."""
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_DRAFT, config.STATUS_REJECTED, config.STATUS_IN_PROGRESS),
            f"Không thể sửa công việc ở trạng thái '{_status_label(task['status'])}'.",
        )
        permissions.require(
            permissions.can_edit_task(actor, task), "Bạn không có quyền sửa công việc này."
        )

        changes: Dict[str, Any] = {}
        log_detail: Dict[str, Any] = {}

        new_title = _clean_text(title, "Tên công việc", required=True, max_len=config.MAX_TITLE_LENGTH)
        if new_title != task["title"]:
            changes["title"] = new_title
            log_detail["title"] = {"old": task["title"], "new": new_title}

        new_description = _clean_text(
            description, "Mô tả", required=False, max_len=config.MAX_TEXT_LENGTH
        )
        if new_description != (task["description"] or None):
            changes["description"] = new_description
            log_detail["description"] = {"old": task["description"], "new": new_description}

        new_deadline = _parse_deadline(deadline)
        if new_deadline != task["deadline"]:
            _check_not_past(new_deadline)
            changes["deadline"] = new_deadline
            log_detail["deadline"] = {"old": task["deadline"], "new": new_deadline}

        if task["task_type"] == config.TYPE_TEAM:
            if lead_id is None:
                raise ValueError("Cần chọn người chủ trì.")
            if lead_id != task["lead_id"]:
                new_lead = _require_assignable(conn, lead_id, "Người chủ trì")
                old_lead = conn.execute(
                    "SELECT full_name FROM users WHERE id = ?", (task["lead_id"],)
                ).fetchone()
                changes["lead_id"] = lead_id
                log_detail["lead"] = {
                    "old": old_lead["full_name"] if old_lead else None,
                    "new": new_lead["full_name"],
                }
        elif lead_id is not None and lead_id != task["lead_id"]:
            if task["task_type"] == config.TYPE_DIRECT:
                raise ValueError(
                    "Không đổi người được giao của công việc giao trực tiếp. "
                    "Hãy tạo công việc mới nếu cần giao người khác."
                )
            raise ValueError(
                "Không đổi người thực hiện của công việc cá nhân. Hãy tạo công việc mới nếu cần đổi người."
            )

        if not changes:
            return False

        assert all(col in _EDITABLE_COLUMNS for col in changes)
        set_clause = ", ".join(f"{col} = ?" for col in changes)
        conn.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = ? WHERE id = ?",
            (*changes.values(), now_iso(), task_id),
        )
        add_log(conn, task_id, actor["id"], config.LOG_TASK_UPDATED, log_detail)
        return True


# --- Quy trình phê duyệt -----------------------------------------------
def submit_task(actor: dict, task_id: int) -> None:
    """Bản nháp / bị từ chối -> Chờ phê duyệt."""
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_DRAFT, config.STATUS_REJECTED),
            "Chỉ gửi phê duyệt được công việc ở trạng thái 'Bản nháp' hoặc 'Bị từ chối'.",
        )
        permissions.require(
            permissions.can_submit_task(actor, task),
            "Chỉ người đề xuất hoặc trưởng phòng mới được gửi phê duyệt công việc này.",
        )

        _require_assignable(conn, task["lead_id"], "Người chủ trì")
        members = conn.execute(
            "SELECT m.user_id, u.full_name, u.is_active, u.role FROM task_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.task_id = ? ORDER BY m.id",
            (task_id,),
        ).fetchall()
        if not members:
            raise ValueError("Cần phân công ít nhất một thành viên trước khi gửi phê duyệt.")
        unavailable = [
            m["full_name"]
            for m in members
            if not m["is_active"] or m["role"] not in config.ASSIGNABLE_ROLES
        ]
        if unavailable:
            raise ValueError(
                "Có thành viên không còn khả dụng, hãy xóa hoặc thay phần việc: " + ", ".join(unavailable)
            )
        if task["task_type"] == config.TYPE_PERSONAL and (
            len(members) != 1 or members[0]["user_id"] != task["proposer_id"] or task["lead_id"] != task["proposer_id"]
        ):
            raise ValueError("Công việc cá nhân phải có đúng một phần việc của người đề xuất.")
        if task["deadline"] < today_vn().isoformat():
            raise ValueError("Hạn hoàn thành đã qua, hãy cập nhật hạn trước khi gửi phê duyệt.")

        now = now_iso()
        conn.execute(
            "UPDATE tasks SET status = ?, submitted_at = ?, reject_reason = NULL, updated_at = ? WHERE id = ?",
            (config.STATUS_PENDING_APPROVAL, now, now, task_id),
        )
        add_log(
            conn,
            task_id,
            actor["id"],
            config.LOG_SUBMITTED,
            {"from": task["status"], "resubmitted": task["status"] == config.STATUS_REJECTED},
        )


def approve_task(actor: dict, task_id: int) -> None:
    """Chờ phê duyệt -> Đang thực hiện."""
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_PENDING_APPROVAL,),
            "Chỉ phê duyệt được công việc đang ở trạng thái 'Chờ phê duyệt'.",
        )
        permissions.require(
            permissions.can_decide_task(actor, task), "Chỉ trưởng phòng mới được phê duyệt."
        )
        now = now_iso()
        conn.execute(
            "UPDATE tasks SET status = ?, approver_id = ?, approved_at = ?, updated_at = ? WHERE id = ?",
            (config.STATUS_IN_PROGRESS, actor["id"], now, now, task_id),
        )
        add_log(conn, task_id, actor["id"], config.LOG_APPROVED, {"auto": False})


def reject_task(actor: dict, task_id: int, reason: str) -> None:
    """Chờ phê duyệt -> Bị từ chối (bắt buộc có lý do)."""
    clean_reason = _clean_text(reason, "Lý do từ chối", required=True, max_len=config.MAX_PART_LENGTH)
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_PENDING_APPROVAL,),
            "Chỉ từ chối được công việc đang ở trạng thái 'Chờ phê duyệt'.",
        )
        permissions.require(
            permissions.can_decide_task(actor, task), "Chỉ trưởng phòng mới được từ chối."
        )
        conn.execute(
            "UPDATE tasks SET status = ?, approver_id = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
            (config.STATUS_REJECTED, actor["id"], clean_reason, now_iso(), task_id),
        )
        add_log(conn, task_id, actor["id"], config.LOG_REJECTED, {"reason": clean_reason})


# --- Phân công phần việc -----------------------------------------------
def _task_for_allocation(
    conn: sqlite3.Connection, actor: dict, task_id: int, *, structural: bool = False
) -> dict:
    """Lấy công việc để sửa phân công. `structural`: thêm/xóa phần việc (không áp dụng cho việc giao trực tiếp)."""
    task = dict(_task_row(conn, task_id))
    _expect_status(
        task,
        _ALLOCATION_STATUSES,
        f"Không thể thay đổi phân công khi công việc ở trạng thái '{_status_label(task['status'])}'.",
    )
    permissions.require(
        permissions.can_edit_allocation(actor, task),
        "Bạn không có quyền thay đổi phân công của công việc này.",
    )
    if structural and task["task_type"] in config.SINGLE_ASSIGNEE_TASK_TYPES:
        raise ValueError("Công việc một người chỉ có một phần việc, không thêm hoặc xóa phần việc.")
    return task


def add_member(
    actor: dict, task_id: int, user_id: int, content: str, quantity: Any = None, unit: Optional[str] = None
) -> int:
    with database.transaction() as conn:
        _task_for_allocation(conn, actor, task_id, structural=True)
        member_id = _insert_member(conn, task_id, actor["id"], user_id, content, quantity, unit)
        _touch_task(conn, task_id)
        return member_id


def add_members_bulk(
    actor: dict,
    task_id: int,
    content: str,
    unit: Optional[str],
    rows: Sequence[dict],
) -> List[int]:
    """Phân cùng một loại việc cho nhiều người, chỉ khác khối lượng.

    `rows`: danh sách dict {user_id, quantity}. Nội dung và đơn vị dùng chung.
    Tất cả hoặc không có gì: nếu một dòng sai thì không dòng nào được lưu.
    Mỗi người vẫn là một phần việc riêng và có một dòng nhật ký riêng.
    """
    if not rows:
        raise ValueError("Cần chọn ít nhất một thành viên.")
    user_ids = [r.get("user_id") for r in rows]
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("Mỗi người chỉ có một phần việc trong một công việc.")
    # Kiểm tra nội dung/đơn vị một lần trước khi mở transaction.
    _normalize_part(content, None, unit)
    with database.transaction() as conn:
        _task_for_allocation(conn, actor, task_id, structural=True)
        member_ids = []
        for row in rows:
            member_ids.append(
                _insert_member(
                    conn, task_id, actor["id"], row.get("user_id"), content, row.get("quantity"), unit
                )
            )
        _touch_task(conn, task_id)
        return member_ids


def _member_row(conn: sqlite3.Connection, member_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT m.*, u.full_name FROM task_members m JOIN users u ON u.id = m.user_id WHERE m.id = ?",
        (member_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy phần việc.")
    return row


def update_member(
    actor: dict, member_id: int, content: str, quantity: Any = None, unit: Optional[str] = None
) -> bool:
    """Sửa nội dung/khối lượng/đơn vị của một phần việc. False nếu không có thay đổi."""
    with database.transaction() as conn:
        member = _member_row(conn, member_id)
        _task_for_allocation(conn, actor, member["task_id"])
        clean_content, clean_quantity, clean_unit = _normalize_part(content, quantity, unit)

        log_detail: Dict[str, Any] = {"user_name": member["full_name"]}
        changed = False
        for field, new_value in (
            ("content", clean_content),
            ("quantity", clean_quantity),
            ("unit", clean_unit),
        ):
            old_value = member[field] if member[field] not in ("",) else None
            if new_value != old_value:
                changed = True
                log_detail[field] = {"old": old_value, "new": new_value}
        # Đổi khối lượng giao thì tiến độ % phải tính lại theo khối lượng đã hoàn thành.
        old_done, old_progress = member["done_quantity"], member["progress"]
        if has_quantity(clean_quantity):
            new_done = old_done if old_done is not None else clean_quantity * old_progress / 100.0
            new_progress = compute_progress(new_done, clean_quantity)
        else:  # bỏ khối lượng giao: quay về nhập tiến độ trực tiếp theo %, giữ nguyên % hiện có
            new_done, new_progress = None, old_progress
        if new_done != old_done and (old_done is not None or new_done is not None):
            log_detail["done_quantity"] = {"old": old_done, "new": new_done}
            changed = True
        if new_progress != old_progress:
            log_detail["progress"] = {"old": old_progress, "new": new_progress}
            changed = True
        if not changed:
            return False

        now = now_iso()
        conn.execute(
            "UPDATE task_members SET content = ?, quantity = ?, unit = ?, done_quantity = ?, progress = ?, "
            "updated_at = ? WHERE id = ?",
            (clean_content, clean_quantity, clean_unit, new_done, new_progress, now, member_id),
        )
        _touch_task(conn, member["task_id"])
        add_log(
            conn,
            member["task_id"],
            actor["id"],
            config.LOG_MEMBER_UPDATED,
            log_detail,
            member_id=member_id,
        )
        return True


def remove_member(actor: dict, member_id: int) -> None:
    """Xóa phần việc. Không cho xóa nếu thành viên đã cập nhật tiến độ hoặc báo cáo kết quả."""
    with database.transaction() as conn:
        member = _member_row(conn, member_id)
        _task_for_allocation(conn, actor, member["task_id"], structural=True)
        if member["progress"] > 0 or (member["result_report"] or "").strip():
            raise ValueError(
                "Thành viên đã cập nhật tiến độ hoặc báo cáo kết quả, không thể xóa phần việc."
            )
        conn.execute("DELETE FROM task_members WHERE id = ?", (member_id,))
        _touch_task(conn, member["task_id"])
        add_log(
            conn,
            member["task_id"],
            actor["id"],
            config.LOG_MEMBER_REMOVED,
            {
                "user_id": member["user_id"],
                "user_name": member["full_name"],
                "content": member["content"],
                "quantity": member["quantity"],
                "unit": member["unit"],
            },
            member_id=member_id,
        )


# --- Giai đoạn 3: tiến độ, nhận xét, báo cáo hoàn thành, đánh giá --------
def _normalize_progress(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Tiến độ phải là một số từ 0 đến 100.") from None
    if not math.isfinite(number) or number != int(number) or not 0 <= number <= 100:
        raise ValueError("Tiến độ phải là số nguyên từ 0 đến 100.")
    return int(number)


def _normalize_done_quantity(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Khối lượng đã hoàn thành phải là một số.") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError("Khối lượng đã hoàn thành phải là số không âm.")
    return number


def _all_parts_done(conn: sqlite3.Connection, task_id: int) -> Optional[str]:
    """None nếu mọi phần việc đã xong (100% và có báo cáo kết quả); ngược lại trả về lý do."""
    rows = conn.execute(
        "SELECT u.full_name, m.progress, m.result_report FROM task_members m "
        "JOIN users u ON u.id = m.user_id WHERE m.task_id = ? ORDER BY m.id",
        (task_id,),
    ).fetchall()
    if not rows:
        return "Công việc chưa có phần việc nào."
    pending = [
        r["full_name"]
        for r in rows
        if r["progress"] < 100 or not (r["result_report"] or "").strip()
    ]
    if pending:
        return "Còn phần việc chưa hoàn thành hoặc chưa có báo cáo kết quả: " + ", ".join(pending) + "."
    return None


def update_progress(actor: dict, member_id: int, value: Any, result_report: Optional[str]) -> bool:
    """Thành viên cập nhật tiến độ và báo cáo kết quả của chính phần việc của mình.

    - Phần việc CÓ khối lượng giao: `value` là khối lượng đã hoàn thành (số tuyệt đối, cùng đơn vị
      với khối lượng giao, được phép vượt). Chương trình tự tính tiến độ % = đã hoàn thành / được giao.
    - Phần việc KHÔNG có khối lượng giao: `value` là tiến độ % (số nguyên 0-100).
    - Tiến độ đạt 100% thì bắt buộc có báo cáo kết quả.
    - Công việc một người (DIRECT hoặc PERSONAL): khi phần việc xong (100% và có báo cáo) thì
      tự chuyển sang "Đã báo cáo, chờ đánh giá". Công việc TEAM do người chủ trì báo cáo (report_completion).
    - Gửi lại đúng giá trị cũ vẫn kiểm tra tự động báo cáo (dùng sau khi mở lại công việc).
    Trả về True nếu có thay đổi dữ liệu.
    """
    new_report = _clean_text(
        result_report, "Báo cáo kết quả", required=False, max_len=config.MAX_TEXT_LENGTH
    )

    with database.transaction() as conn:
        member = _member_row(conn, member_id)
        task = dict(_task_row(conn, member["task_id"]))
        _expect_status(
            task,
            (config.STATUS_IN_PROGRESS,),
            f"Chỉ cập nhật tiến độ khi công việc đang thực hiện (hiện: '{_status_label(task['status'])}').",
        )
        permissions.require(
            permissions.can_update_progress(actor, task, member),
            "Bạn chỉ được cập nhật tiến độ phần việc của chính mình.",
        )

        by_quantity = has_quantity(member["quantity"])
        if by_quantity:
            new_done = _normalize_done_quantity(value)
            new_progress = compute_progress(new_done, member["quantity"])
        else:
            new_done = None
            new_progress = _normalize_progress(value)
        if new_progress == 100 and not new_report:
            raise ValueError("Khi tiến độ đạt 100%, cần nhập báo cáo kết quả.")

        detail: Dict[str, Any] = {"user_name": member["full_name"]}
        if by_quantity:
            detail["quantity"] = member["quantity"]
            detail["unit"] = member["unit"]
            if new_done != member["done_quantity"]:
                detail["done_quantity"] = {"old": member["done_quantity"], "new": new_done}
        if new_progress != member["progress"]:
            detail["progress"] = {"old": member["progress"], "new": new_progress}
        if new_report != ((member["result_report"] or "").strip() or None):
            detail["result_report"] = {"old": member["result_report"], "new": new_report}
        changed = any(isinstance(v, dict) for v in detail.values())
        if changed:
            now = now_iso()
            conn.execute(
                "UPDATE task_members SET done_quantity = ?, progress = ?, result_report = ?, updated_at = ? "
                "WHERE id = ?",
                (new_done, new_progress, new_report, now, member_id),
            )
            _touch_task(conn, task["id"])
            add_log(
                conn, task["id"], actor["id"], config.LOG_MEMBER_PROGRESS, detail, member_id=member_id
            )

        if task["task_type"] in config.SINGLE_ASSIGNEE_TASK_TYPES and new_progress == 100:
            if _all_parts_done(conn, task["id"]) is None:
                now = now_iso()
                conn.execute(
                    "UPDATE tasks SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                    (config.STATUS_REPORTED, now, now, task["id"]),
                )
                add_log(conn, task["id"], actor["id"], config.LOG_REPORTED, {"auto": True})
        return changed


def comment_member(actor: dict, member_id: int, comment: Optional[str]) -> bool:
    """Người chủ trì (hoặc trưởng phòng) nhận xét một phần việc. Để trống để xóa nhận xét."""
    new_comment = _clean_text(comment, "Nhận xét", required=False, max_len=config.MAX_PART_LENGTH)
    with database.transaction() as conn:
        member = _member_row(conn, member_id)
        task = dict(_task_row(conn, member["task_id"]))
        _expect_status(
            task,
            (config.STATUS_IN_PROGRESS, config.STATUS_REPORTED),
            "Chỉ nhận xét được khi công việc đang thực hiện hoặc chờ đánh giá.",
        )
        permissions.require(
            permissions.can_comment_member(actor, task),
            "Chỉ người chủ trì hoặc trưởng phòng mới được nhận xét phần việc.",
        )
        old_comment = (member["lead_comment"] or "").strip() or None
        if new_comment == old_comment:
            return False
        conn.execute(
            "UPDATE task_members SET lead_comment = ?, updated_at = ? WHERE id = ?",
            (new_comment, now_iso(), member_id),
        )
        _touch_task(conn, task["id"])
        add_log(
            conn,
            task["id"],
            actor["id"],
            config.LOG_MEMBER_COMMENT,
            {"user_name": member["full_name"], "comment": {"old": old_comment, "new": new_comment}},
            member_id=member_id,
        )
        return True


def report_completion(actor: dict, task_id: int, summary: str) -> None:
    """Người chủ trì báo cáo hoàn thành công việc nhóm: Đang thực hiện -> Đã báo cáo, chờ đánh giá."""
    clean_summary = _clean_text(
        summary, "Báo cáo tổng hợp", required=True, max_len=config.MAX_TEXT_LENGTH
    )
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_IN_PROGRESS,),
            "Chỉ báo cáo hoàn thành được khi công việc đang thực hiện.",
        )
        if task["task_type"] != config.TYPE_TEAM:
            raise ValueError(
                "Công việc một người tự chuyển sang chờ đánh giá khi hoàn thành phần việc."
            )
        permissions.require(
            permissions.can_report_completion(actor, task),
            "Chỉ người chủ trì mới được báo cáo hoàn thành công việc này.",
        )
        problem = _all_parts_done(conn, task_id)
        if problem:
            raise ValueError(problem)
        now = now_iso()
        conn.execute(
            "UPDATE tasks SET status = ?, report_summary = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (config.STATUS_REPORTED, clean_summary, now, now, task_id),
        )
        add_log(conn, task_id, actor["id"], config.LOG_REPORTED, {"auto": False, "summary": clean_summary})


def evaluate_task(actor: dict, task_id: int, result: str, comment: Optional[str]) -> None:
    """Trưởng phòng đánh giá kết quả tổng thể: Đã báo cáo -> Đạt / Không đạt (Không đạt bắt buộc có nhận xét)."""
    if result not in config.EVAL_RESULTS:
        raise ValueError("Kết quả đánh giá không hợp lệ.")
    clean_comment = _clean_text(
        comment,
        "Nhận xét đánh giá",
        required=result == config.EVAL_FAIL,
        max_len=config.MAX_TEXT_LENGTH,
    )
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_REPORTED,),
            "Chỉ đánh giá được công việc đã báo cáo hoàn thành.",
        )
        permissions.require(
            permissions.can_evaluate(actor, task), "Chỉ trưởng phòng mới được đánh giá kết quả."
        )
        status = config.STATUS_PASSED if result == config.EVAL_PASS else config.STATUS_FAILED
        now = now_iso()
        conn.execute(
            "UPDATE tasks SET status = ?, eval_result = ?, eval_comment = ?, evaluated_by = ?, "
            "evaluated_at = ?, updated_at = ? WHERE id = ?",
            (status, result, clean_comment, actor["id"], now, now, task_id),
        )
        add_log(
            conn, task_id, actor["id"], config.LOG_EVALUATED, {"result": result, "comment": clean_comment}
        )


def reopen_task(actor: dict, task_id: int, reason: str) -> None:
    """Trưởng phòng mở lại công việc 'Không đạt' để làm tiếp: Không đạt -> Đang thực hiện.

    Kết quả đánh giá cũ được xóa khỏi công việc (vẫn còn đầy đủ trong nhật ký).
    """
    clean_reason = _clean_text(reason, "Lý do mở lại", required=True, max_len=config.MAX_PART_LENGTH)
    with database.transaction() as conn:
        task = dict(_task_row(conn, task_id))
        _expect_status(
            task,
            (config.STATUS_FAILED,),
            "Chỉ mở lại được công việc đã đánh giá 'Không đạt'.",
        )
        permissions.require(
            permissions.can_reopen(actor, task), "Chỉ trưởng phòng mới được mở lại công việc."
        )
        conn.execute(
            "UPDATE tasks SET status = ?, eval_result = NULL, eval_comment = NULL, evaluated_by = NULL, "
            "evaluated_at = NULL, completed_at = NULL, report_summary = NULL, updated_at = ? WHERE id = ?",
            (config.STATUS_IN_PROGRESS, now_iso(), task_id),
        )
        add_log(
            conn,
            task_id,
            actor["id"],
            config.LOG_REOPENED,
            {"reason": clean_reason, "previous_eval_comment": task["eval_comment"]},
        )


# --- Truy vấn ------------------------------------------------------------
def is_overdue(task: dict) -> bool:
    return bool(task.get("deadline")) and (
        task["status"] in config.OPEN_STATUSES and task["deadline"] < today_vn().isoformat()
    )


def list_assignable_users() -> List[dict]:
    placeholders = ", ".join("?" for _ in config.ASSIGNABLE_ROLES)
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT id, username, full_name, role FROM users "
            f"WHERE is_active = 1 AND role IN ({placeholders}) "
            "ORDER BY full_name COLLATE NOCASE, id",
            tuple(config.ASSIGNABLE_ROLES),
        ).fetchall()
    return [dict(r) for r in rows]


_TASK_SELECT = """
SELECT t.*,
       p.full_name AS proposer_name,
       l.full_name AS lead_name,
       a.full_name AS approver_name
FROM tasks t
JOIN users p ON p.id = t.proposer_id
LEFT JOIN users l ON l.id = t.lead_id
LEFT JOIN users a ON a.id = t.approver_id
"""


def get_task(task_id: int) -> Optional[dict]:
    """Chi tiết một công việc kèm danh sách phần việc. Mọi người dùng đều được xem."""
    with database.connect() as conn:
        row = conn.execute(_TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = dict(row)
        members = conn.execute(
            "SELECT m.*, u.full_name, u.username FROM task_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.task_id = ? ORDER BY m.id",
            (task_id,),
        ).fetchall()
    task["members"] = [dict(m) for m in members]
    task["overdue"] = is_overdue(task)
    return task


def list_tasks(
    *,
    search: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
    task_type: Optional[str] = None,
    involved_user_id: Optional[int] = None,
    overdue_only: bool = False,
) -> List[dict]:
    """Danh sách công việc (mới cập nhật trước). Mọi người dùng đều được xem tất cả.

    `involved_user_id`: chỉ việc người đó đề xuất, chủ trì hoặc có phần việc.
    """
    where: List[str] = []
    params: List[Any] = []
    if statuses:
        where.append(f"t.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if task_type:
        where.append("t.task_type = ?")
        params.append(task_type)
    if involved_user_id is not None:
        where.append(
            "(t.proposer_id = ? OR t.lead_id = ? OR EXISTS "
            "(SELECT 1 FROM task_members m WHERE m.task_id = t.id AND m.user_id = ?))"
        )
        params.extend([involved_user_id] * 3)

    sql = (
        "SELECT t.id, t.title, t.description, t.task_type, t.status, t.deadline, t.updated_at, "
        "p.full_name AS proposer_name, l.full_name AS lead_name, "
        "(SELECT COUNT(*) FROM task_members m WHERE m.task_id = t.id) AS member_count, "
        "(SELECT AVG(m.progress) FROM task_members m WHERE m.task_id = t.id) AS avg_progress "
        "FROM tasks t JOIN users p ON p.id = t.proposer_id "
        "LEFT JOIN users l ON l.id = t.lead_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.updated_at DESC, t.id DESC"

    with database.connect() as conn:
        tasks = [dict(r) for r in conn.execute(sql, params).fetchall()]

    needle = (search or "").strip().casefold()
    if needle:
        tasks = [
            t
            for t in tasks
            if needle in (t["title"] or "").casefold() or needle in (t["description"] or "").casefold()
        ]
    for t in tasks:
        t["overdue"] = is_overdue(t)
    if overdue_only:
        tasks = [t for t in tasks if t["overdue"]]
    return tasks


def list_pending_approvals() -> List[dict]:
    """Các công việc đang chờ phê duyệt, gửi sớm nhất trước."""
    with database.connect() as conn:
        rows = conn.execute(
            _TASK_SELECT + " WHERE t.status = ? ORDER BY t.submitted_at, t.id",
            (config.STATUS_PENDING_APPROVAL,),
        ).fetchall()
    tasks = [dict(r) for r in rows]
    for t in tasks:
        t["overdue"] = is_overdue(t)
    return tasks


def list_pending_evaluations() -> List[dict]:
    """Các công việc đã báo cáo hoàn thành, chờ trưởng phòng đánh giá (báo cáo sớm nhất trước)."""
    with database.connect() as conn:
        rows = conn.execute(
            _TASK_SELECT + " WHERE t.status = ? ORDER BY t.completed_at, t.id",
            (config.STATUS_REPORTED,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_my_work(user_id: int) -> Dict[str, List[dict]]:
    """Việc của một người, chia theo vai trò trong từng công việc.

    - parts: phần việc của tôi trong các công việc chưa kết thúc (Đang thực hiện / Đã báo cáo)
    - leading: công việc nhóm tôi chủ trì (việc giao trực tiếp nằm ở `parts`) (đang thực hiện / đã báo cáo)
    - proposed: công việc tôi đề xuất còn ở bước nháp, chờ duyệt hoặc bị từ chối
    """
    with database.connect() as conn:
        parts = conn.execute(
            "SELECT m.id AS member_id, m.content, m.quantity, m.unit, m.done_quantity, m.progress, m.result_report, "
            "m.lead_comment, t.id AS task_id, t.title, t.status, t.task_type, t.deadline, "
            "l.full_name AS lead_name "
            "FROM task_members m JOIN tasks t ON t.id = m.task_id "
            "LEFT JOIN users l ON l.id = t.lead_id "
            "WHERE m.user_id = ? AND t.status IN (?, ?) ORDER BY t.deadline, t.id",
            (user_id, config.STATUS_IN_PROGRESS, config.STATUS_REPORTED),
        ).fetchall()
        leading = conn.execute(
            "SELECT t.id, t.title, t.status, t.deadline, "
            "(SELECT COUNT(*) FROM task_members m WHERE m.task_id = t.id) AS member_count, "
            "(SELECT AVG(m.progress) FROM task_members m WHERE m.task_id = t.id) AS avg_progress "
            "FROM tasks t WHERE t.lead_id = ? AND t.task_type = 'TEAM' AND t.status IN (?, ?) ORDER BY t.deadline, t.id",
            (user_id, config.STATUS_IN_PROGRESS, config.STATUS_REPORTED),
        ).fetchall()
        proposed = conn.execute(
            "SELECT t.id, t.title, t.status, t.deadline, t.reject_reason FROM tasks t "
            "WHERE t.proposer_id = ? AND t.status IN (?, ?, ?) ORDER BY t.updated_at DESC, t.id DESC",
            (user_id, config.STATUS_DRAFT, config.STATUS_PENDING_APPROVAL, config.STATUS_REJECTED),
        ).fetchall()
    result = {
        "parts": [dict(r) for r in parts],
        "leading": [dict(r) for r in leading],
        "proposed": [dict(r) for r in proposed],
    }
    for group in result.values():
        for item in group:
            item["overdue"] = is_overdue(
                {"status": item["status"], "deadline": item["deadline"]}
            )
    return result
