"""Phê duyệt: trưởng phòng duyệt/từ chối công việc đang chờ và đánh giá công việc đã báo cáo."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import permissions, task_service
from ui import components as c
from ui import task_detail

FLASH_KEY = "approval_flash"


def _members(task: dict) -> None:
    if not task["members"]:
        st.caption("Chưa có phần việc nào.")
        return
    df = pd.DataFrame(
        [
            {
                "Thành viên": m["full_name"],
                "Nội dung phần việc": m["content"],
                "Khối lượng": c.fmt_quantity(m["quantity"], m["unit"]),
            }
            for m in task["members"]
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)


def _render_approvals(user: dict) -> None:
    pending = task_service.list_pending_approvals()
    if not permissions.is_manager(user):
        st.info("Chỉ trưởng phòng mới được phê duyệt hoặc từ chối. Dưới đây là các công việc đang chờ phê duyệt.")
    if not pending:
        st.success("Không có công việc nào đang chờ phê duyệt.")
        return
    st.caption(f"{len(pending)} công việc chờ phê duyệt")

    for item in pending:
        task = task_service.get_task(item["id"])
        label = f"#{task['id']} · {task['title']} · hạn {c.fmt_date(task['deadline'])}"
        with st.expander(label + (" ⚠" if task["overdue"] else ""), expanded=len(pending) == 1):
            task_detail.render_summary(task)
            _members(task)
            if not permissions.can_decide_task(user, task):
                continue
            col_ok, col_no = st.columns(2)
            if col_ok.button("Phê duyệt", key=f"approve_{task['id']}", type="primary", width="stretch"):
                if c.run_action(
                    task_service.approve_task,
                    user,
                    task["id"],
                    success=f"Đã phê duyệt công việc #{task['id']}.",
                    flash_key=FLASH_KEY,
                ):
                    st.rerun()
            with col_no.popover("Từ chối", width="stretch"):
                reason = st.text_area("Lý do từ chối (bắt buộc)", key=f"reason_{task['id']}")
                if st.button("Xác nhận từ chối", key=f"reject_{task['id']}", width="stretch"):
                    if c.run_action(
                        task_service.reject_task,
                        user,
                        task["id"],
                        reason,
                        success=f"Đã từ chối công việc #{task['id']}.",
                        flash_key=FLASH_KEY,
                    ):
                        st.rerun()


def _render_evaluations(user: dict) -> None:
    reported = task_service.list_pending_evaluations()
    if not permissions.is_manager(user):
        st.info("Chỉ trưởng phòng mới được đánh giá. Dưới đây là các công việc đã báo cáo, đang chờ đánh giá.")
    if not reported:
        st.success("Không có công việc nào đang chờ đánh giá.")
        return
    st.caption(f"{len(reported)} công việc chờ đánh giá")

    for item in reported:
        task = task_service.get_task(item["id"])
        label = f"#{task['id']} · {task['title']} · hạn {c.fmt_date(task['deadline'])}"
        with st.expander(label + (" ⚠" if task["overdue"] else ""), expanded=len(reported) == 1):
            task_detail.render_summary(task)
            task_detail.render_outcome(task)
            task_detail._members_table(task)
            task_detail.render_evaluation_controls(user, task, "approvals", flash_key=FLASH_KEY)


def render(user: dict) -> None:
    st.header("Phê duyệt")
    c.show_flash(FLASH_KEY)
    pending_count = len(task_service.list_pending_approvals())
    eval_count = len(task_service.list_pending_evaluations())
    tab_approve, tab_eval = st.tabs(
        [f"Chờ phê duyệt ({pending_count})", f"Chờ đánh giá ({eval_count})"]
    )
    with tab_approve:
        _render_approvals(user)
    with tab_eval:
        _render_evaluations(user)
