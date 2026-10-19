"""Danh sách công việc: bộ lọc, bảng, tạo mới và xem chi tiết."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

import config
from core import permissions, task_service
from core.timeutil import today_vn
from ui import components as c
from ui import task_detail

SELECTED_KEY = "selected_task_id"


def _person_label(people):
    names = {p["id"]: p["full_name"] for p in people}
    return lambda i: names[i]


def _create_form(user: dict) -> None:
    can_direct = permissions.can_create_task(user, config.TYPE_DIRECT)
    can_team = permissions.can_create_task(user, config.TYPE_TEAM)
    can_personal = permissions.can_create_task(user, config.TYPE_PERSONAL)
    if not (can_direct or can_team or can_personal):
        return

    people = task_service.list_assignable_users()
    ids = [p["id"] for p in people]
    label = _person_label(people)

    with st.expander("Tạo công việc mới"):
        types = [
            t
            for t, ok in (
                (config.TYPE_TEAM, can_team),
                (config.TYPE_DIRECT, can_direct),
                (config.TYPE_PERSONAL, can_personal),
            )
            if ok
        ]
        task_type = st.radio("Loại công việc", types, format_func=c.type_label, horizontal=True, key="new_type")
        if task_type == config.TYPE_DIRECT:
            st.caption(
                "Giao trực tiếp cho một người: vào thẳng trạng thái 'Đang thực hiện', không cần phê duyệt. "
                "Người được giao là người chủ trì và có một phần việc."
            )
        elif task_type == config.TYPE_PERSONAL:
            st.caption(
                "Công việc cá nhân do bạn thực hiện. Sau khi tạo, công việc sẽ được gửi trưởng phòng "
                "phê duyệt; được duyệt xong bạn có thể cập nhật tiến độ."
            )
        else:
            st.caption(
                "Công việc nhóm: lưu ở dạng bản nháp. Sau khi tạo, chủ trì phân phần việc cho thành viên "
                "rồi gửi phê duyệt; khi công việc bắt đầu, chủ trì cập nhật tiến độ thay cho các thành viên."
            )

        with st.form("create_task_form", clear_on_submit=True):
            title = st.text_input("Tên công việc", max_chars=config.MAX_TITLE_LENGTH)
            description = st.text_area("Mô tả", max_chars=config.MAX_TEXT_LENGTH)
            deadline = st.date_input("Hạn hoàn thành", value=today_vn() + timedelta(days=7), min_value=today_vn())

            lead_id = None
            part_content = part_unit = ""
            part_quantity = 0.0
            if task_type == config.TYPE_TEAM:
                default = ids.index(user["id"]) if user["id"] in ids else 0
                lead_id = st.selectbox("Người chủ trì", ids, index=default, format_func=label)
            elif task_type == config.TYPE_DIRECT:
                lead_id = st.selectbox(
                    "Giao cho (người chủ trì)", ids, index=None, format_func=label, placeholder="Chọn một người"
                )
            else:
                lead_id = user["id"]
            if task_type in config.SINGLE_ASSIGNEE_TASK_TYPES:
                content_label = (
                    "Nội dung phần việc cá nhân"
                    if task_type == config.TYPE_PERSONAL
                    else "Nội dung công việc giao"
                )
                part_content = st.text_area(content_label, max_chars=config.MAX_PART_LENGTH)
                part_quantity = st.number_input("Khối lượng (0 = không đặt khối lượng)", min_value=0.0, value=0.0, step=1.0)
                part_unit = st.text_input("Đơn vị", max_chars=config.MAX_UNIT_LENGTH)
            submitted = st.form_submit_button("Tạo công việc", type="primary", width="stretch")

        if submitted:
            members = []
            if task_type in config.SINGLE_ASSIGNEE_TASK_TYPES:
                members = [{"user_id": lead_id, "content": part_content, "quantity": part_quantity or None, "unit": part_unit}]
            try:
                new_id = task_service.create_task(
                    user, task_type, title, description, deadline, lead_id=lead_id, members=members
                )
            except (ValueError, permissions.PermissionDenied) as exc:
                st.error(str(exc))
            else:
                st.session_state[SELECTED_KEY] = new_id
                c.flash(task_detail.FLASH_KEY, f"Đã tạo công việc #{new_id}.")
                st.rerun()


def _filters(user: dict):
    col1, col2, col3 = st.columns([2, 2, 2])
    search = col1.text_input("Tìm kiếm", placeholder="Tên hoặc mô tả công việc")
    statuses = col2.multiselect("Trạng thái", config.STATUSES, format_func=c.status_label)
    task_type = col3.selectbox(
        "Loại", [None, *config.TASK_TYPES], format_func=lambda t: "Tất cả" if t is None else c.type_label(t)
    )
    col4, col5 = st.columns(2)
    mine = col4.checkbox("Chỉ việc liên quan đến tôi")
    overdue_only = col5.checkbox("Chỉ việc quá hạn")
    return {
        "search": search,
        "statuses": statuses or None,
        "task_type": task_type,
        "involved_user_id": user["id"] if mine else None,
        "overdue_only": overdue_only,
    }


def render(user: dict) -> None:
    st.header("Danh sách công việc")
    c.show_flash(task_detail.FLASH_KEY)
    _create_form(user)

    tasks = task_service.list_tasks(**_filters(user))
    st.caption(f"{len(tasks)} công việc")
    if not tasks:
        st.info("Không có công việc nào khớp bộ lọc.")
        return

    df = pd.DataFrame(
        [
            {
                "Mã": t["id"],
                "Tên công việc": t["title"],
                "Loại": c.type_label(t["task_type"]),
                "Trạng thái": c.status_label(t["status"]),
                "Chủ trì": t["lead_name"] or "",
                "Số người": t["member_count"],
                "Tiến độ TB (%)": round(t["avg_progress"]) if t["avg_progress"] is not None else None,
                "Hạn": c.fmt_date(t["deadline"]) + (" ⚠" if t["overdue"] else ""),
            }
            for t in tasks
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)

    by_id = {t["id"]: t for t in tasks}
    options = list(by_id)
    selected = st.session_state.get(SELECTED_KEY)
    index = options.index(selected) if selected in by_id else None
    chosen = st.selectbox(
        "Xem chi tiết công việc",
        options,
        index=index,
        format_func=lambda i: c.task_option_label(by_id[i]),
        placeholder="Chọn một công việc",
        key="task_picker",
    )
    st.session_state[SELECTED_KEY] = chosen
    if chosen is not None:
        st.divider()
        task_detail.render_detail(user, chosen)
