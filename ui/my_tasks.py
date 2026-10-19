"""Công việc của tôi: phần việc phải làm, việc tôi chủ trì và việc tôi đề xuất."""
from __future__ import annotations

import streamlit as st

import config
from core import permissions, task_service
from ui import components as c
from ui import task_detail


def _deadline_text(item: dict) -> str:
    return f"hạn {c.fmt_date(item['deadline'])}" + (" ⚠ quá hạn" if item.get("overdue") else "")


def _render_parts(user: dict, parts: list) -> None:
    st.subheader(f"Phần việc của tôi ({len(parts)})")
    if not parts:
        st.caption("Bạn chưa có phần việc nào đang thực hiện.")
        return
    for part in parts:
        done_text = c.fmt_done(part)
        title = (
            f"#{part['task_id']} · {part['title']} · {_deadline_text(part)} · "
            + (f"{done_text} · " if done_text else "")
            + f"{part['progress']}%"
        )
        with st.expander(title):
            st.markdown(
                f"**Trạng thái:** {c.status_label(part['status'])}  \n"
                f"**Phần việc:** {part['content']}"
                + (f"  \n**Khối lượng:** {c.fmt_quantity(part['quantity'], part['unit'])}" if part["quantity"] is not None or part["unit"] else "")
                + (f"  \n**Chủ trì:** {part['lead_name']}" if part["lead_name"] else "")
            )
            if part["lead_comment"]:
                st.info(f"Nhận xét: {part['lead_comment']}")
            if part["status"] == config.STATUS_IN_PROGRESS:
                task = task_service.get_task(part["task_id"])
                member = next(m for m in task["members"] if m["id"] == part["member_id"])
                if permissions.can_update_progress(user, task, member):
                    task_detail.render_progress_form(user, task, member, "my")
                elif task["task_type"] == config.TYPE_TEAM:
                    st.caption("Người chủ trì cập nhật tiến độ phần việc nhóm tại chi tiết công việc.")
            else:
                st.caption("Công việc đã báo cáo hoàn thành, đang chờ đánh giá; không cập nhật tiến độ được nữa.")


def _render_leading(leading: list) -> None:
    st.subheader(f"Công việc tôi chủ trì ({len(leading)})")
    if not leading:
        st.caption("Bạn không chủ trì công việc nào đang thực hiện.")
        return
    for item in leading:
        progress = round(item["avg_progress"]) if item["avg_progress"] is not None else 0
        st.markdown(
            f"**#{item['id']} · {item['title']}** · {c.status_label(item['status'])} · "
            f"{item['member_count']} người · tiến độ TB {progress}% · {_deadline_text(item)}"
        )
        st.progress(progress / 100)
    st.caption("Mở công việc ở mục 'Danh sách công việc' để nhận xét thành viên và báo cáo hoàn thành.")


def _render_proposed(proposed: list) -> None:
    st.subheader(f"Công việc tôi đề xuất ({len(proposed)})")
    if not proposed:
        st.caption("Không có công việc đề xuất nào đang ở bước nháp, chờ duyệt hoặc bị từ chối.")
        return
    for item in proposed:
        line = f"**#{item['id']} · {item['title']}** · {c.status_label(item['status'])} · {_deadline_text(item)}"
        st.markdown(line)
        if item["status"] == config.STATUS_REJECTED and item["reject_reason"]:
            st.warning(f"Bị từ chối: {item['reject_reason']}. Sửa lại rồi gửi phê duyệt lại ở mục 'Danh sách công việc'.")


def render(user: dict) -> None:
    st.header("Công việc của tôi")
    c.show_flash(task_detail.FLASH_KEY)
    work = task_service.list_my_work(user["id"])
    if not any(work.values()):
        st.success("Hiện bạn không có công việc nào cần xử lý.")
        return
    _render_parts(user, work["parts"])
    _render_leading(work["leading"])
    _render_proposed(work["proposed"])
