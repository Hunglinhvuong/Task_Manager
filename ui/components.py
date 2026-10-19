"""Thành phần giao diện dùng chung (không chứa SQL hay quy tắc quyền)."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

import streamlit as st

import config
from core import permissions


def status_label(code: str) -> str:
    return config.STATUS_LABELS.get(code, code)


def type_label(code: str) -> str:
    return config.TASK_TYPE_LABELS.get(code, code)


def fmt_date(iso: Optional[str]) -> str:
    """YYYY-MM-DD -> dd/mm/yyyy."""
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def fmt_number(value: Optional[float]) -> str:
    """Số gọn: 5.0 -> '5', 2.5 -> '2.5'."""
    return "" if value is None else f"{value:g}"


def fmt_done(member: Dict[str, Any]) -> str:
    """'12/20 trạm' cho phần việc có khối lượng giao; rỗng nếu tiến độ nhập theo %."""
    if member.get("quantity") is None or member["quantity"] <= 0:
        return ""
    unit = f" {member['unit']}" if member.get("unit") else ""
    return f"{fmt_number(member.get('done_quantity') or 0)}/{fmt_number(member['quantity'])}{unit}"


def fmt_quantity(quantity: Optional[float], unit: Optional[str]) -> str:
    if quantity is None:
        return unit or ""
    text = f"{quantity:g}"
    return f"{text} {unit}" if unit else text


def task_option_label(task: Dict[str, Any]) -> str:
    flag = " ⚠ quá hạn" if task.get("overdue") else ""
    return f"#{task['id']} · {task['title']} · {status_label(task['status'])}{flag}"


# --- Thông báo sau khi thao tác (hiện sau st.rerun) -----------------------
def flash(key: str, message: str) -> None:
    st.session_state[key] = message


def show_flash(key: str) -> None:
    message = st.session_state.pop(key, None)
    if message:
        st.success(message)


def run_action(action, *args, success: Optional[str] = None, flash_key: Optional[str] = None, **kwargs) -> bool:
    """Chạy một thao tác nghiệp vụ, hiện lỗi thân thiện nếu có. Trả về True nếu thành công."""
    try:
        action(*args, **kwargs)
    except (ValueError, permissions.PermissionDenied) as exc:
        st.error(str(exc))
        return False
    if success and flash_key:
        flash(flash_key, success)
    return True


# --- Mô tả dòng nhật ký -----------------------------------------------------
_FIELD_LABELS = {
    "title": "Tên công việc",
    "description": "Mô tả",
    "deadline": "Hạn",
    "lead": "Chủ trì",
    "content": "Nội dung",
    "quantity": "Khối lượng",
    "unit": "Đơn vị",
    "done_quantity": "Đã hoàn thành",
    "progress": "Tiến độ (%)",
}


def _show(value: Any) -> str:
    if value is None or value == "":
        return "(trống)"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    if isinstance(value, str) and len(value) == 10 and value[4:5] == "-":
        return fmt_date(value)
    return str(value)


def describe_log(action: str, detail: Optional[dict]) -> str:
    if not detail:
        return ""
    if action == config.LOG_TASK_CREATED:
        parts = [f"{type_label(detail.get('task_type', ''))}", f"hạn {fmt_date(detail.get('deadline'))}"]
        if detail.get("lead"):
            parts.append(f"chủ trì: {detail['lead']}")
        return "; ".join(parts)
    if action == config.LOG_APPROVED:
        return "Tự động duyệt (trưởng phòng giao trực tiếp)" if detail.get("auto") else ""
    if action == config.LOG_REJECTED:
        return f"Lý do: {detail.get('reason', '')}"
    if action == config.LOG_SUBMITTED:
        return "Gửi lại sau khi bị từ chối" if detail.get("resubmitted") else ""
    if action in (config.LOG_MEMBER_ADDED, config.LOG_MEMBER_REMOVED):
        extra = fmt_quantity(detail.get("quantity"), detail.get("unit"))
        text = f"{detail.get('user_name', '')}: {detail.get('content', '')}"
        return f"{text} ({extra})" if extra else text
    if action == config.LOG_MEMBER_PROGRESS:
        parts = []
        done = detail.get("done_quantity")
        if isinstance(done, dict):
            total = fmt_quantity(detail.get("quantity"), detail.get("unit"))
            parts.append(f"Đã hoàn thành {fmt_number(done.get('new'))}/{total}")
        if isinstance(detail.get("progress"), dict):
            parts.append(f"Tiến độ: {detail['progress']['old']}% → {detail['progress']['new']}%")
        if isinstance(detail.get("result_report"), dict):
            parts.append(f"Báo cáo: {_show(detail['result_report'].get('new'))}")
        return f"{detail.get('user_name', '')} - " + "; ".join(parts)
    if action == config.LOG_MEMBER_COMMENT:
        comment = detail.get("comment") or {}
        return f"{detail.get('user_name', '')} - Nhận xét: {_show(comment.get('new'))}"
    if action == config.LOG_REPORTED:
        if detail.get("auto"):
            return "Tự động: mọi thành viên đã hoàn thành phần việc"
        return f"Báo cáo tổng hợp: {detail.get('summary', '')}"
    if action == config.LOG_EVALUATED:
        text = f"Kết quả: {config.EVAL_LABELS.get(detail.get('result'), detail.get('result'))}"
        return text + (f"; nhận xét: {detail['comment']}" if detail.get("comment") else "")
    if action == config.LOG_REOPENED:
        return f"Lý do: {detail.get('reason', '')}"
    if action in (config.LOG_TASK_UPDATED, config.LOG_MEMBER_UPDATED):
        changes = [
            f"{_FIELD_LABELS.get(field, field)}: {_show(value.get('old'))} → {_show(value.get('new'))}"
            for field, value in detail.items()
            if isinstance(value, dict) and "old" in value
        ]
        prefix = f"{detail['user_name']} - " if detail.get("user_name") else ""
        return prefix + "; ".join(changes)
    return ""
