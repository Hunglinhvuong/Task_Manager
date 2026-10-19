"""Chi tiết một công việc: thông tin, phần việc (phân công) và nhật ký."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import config
from core import log_service, permissions, task_service
from core.timeutil import to_local_str
from ui import components as c

FLASH_KEY = "task_flash"


def _members_table(task: dict) -> None:
    members = task["members"]
    if not members:
        st.caption("Chưa có phần việc nào được phân công.")
        return
    df = pd.DataFrame(
        [
            {
                "Thành viên": m["full_name"],
                "Nội dung phần việc": m["content"],
                "Khối lượng giao": c.fmt_quantity(m["quantity"], m["unit"]),
                "Đã hoàn thành": c.fmt_done(m),
                "Tiến độ": m["progress"],
                "Báo cáo kết quả": m["result_report"] or "",
                "Nhận xét": m["lead_comment"] or "",
            }
            for m in members
        ]
    )
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "Tiến độ": st.column_config.ProgressColumn(
                "Tiến độ", min_value=0, max_value=100, format="%d%%"
            )
        },
    )


def render_progress_form(user: dict, task: dict, member: dict, key_prefix: str) -> None:
    """Nhập tiến độ phần việc theo quyền của công việc.

    Phần việc có khối lượng giao: nhập khối lượng đã hoàn thành (số tuyệt đối), chương trình tự
    tính tiến độ %. Phần việc không có khối lượng giao: nhập tiến độ % trực tiếp.
    """
    key = f"{key_prefix}_progress_{member['id']}"
    by_quantity = task_service.has_quantity(member["quantity"])
    unit = f" {member['unit']}" if member.get("unit") else ""

    if by_quantity:
        assigned = float(member["quantity"])
        value = st.number_input(
            f"Khối lượng đã hoàn thành (trên tổng {c.fmt_number(assigned)}{unit})",
            min_value=0.0,
            value=float(member["done_quantity"] or 0.0),
            step=1.0,
            format="%g",
            key=f"{key}_done",
        )
        percent = task_service.compute_progress(value, assigned)
        st.progress(percent / 100, text=f"Tiến độ tự tính: {percent}%")
        if value > assigned:
            st.caption(f"Đã vượt khối lượng được giao ({c.fmt_number(assigned)}{unit}); tiến độ tính tối đa 100%.")
        elif percent < 100 and value >= assigned * 0.995:
            st.caption("Chưa đủ khối lượng được giao nên tiến độ chưa đạt 100%.")
    else:
        value = st.number_input(
            "Tiến độ (%)",
            min_value=0,
            max_value=100,
            value=int(member["progress"]),
            step=5,
            key=f"{key}_percent",
        )
        st.caption("Phần việc này chưa có khối lượng giao nên nhập tiến độ trực tiếp theo %.")

    report = st.text_area(
        "Báo cáo kết quả",
        value=member["result_report"] or "",
        max_chars=config.MAX_TEXT_LENGTH,
        help="Bắt buộc khi tiến độ đạt 100%.",
        key=f"{key}_report",
    )
    if st.button("Cập nhật tiến độ", type="primary", width="stretch", key=f"{key}_save"):
        try:
            changed = task_service.update_progress(user, member["id"], value, report)
        except (ValueError, permissions.PermissionDenied) as exc:
            st.error(str(exc))
        else:
            c.flash(FLASH_KEY, "Đã cập nhật tiến độ." if changed else "Không có thay đổi nào.")
            st.rerun()


def render_evaluation_controls(user: dict, task: dict, key_prefix: str, flash_key: str = FLASH_KEY) -> None:
    """Trưởng phòng đánh giá công việc đã báo cáo, hoặc mở lại công việc 'Không đạt'."""
    if permissions.can_evaluate(user, task):
        with st.form(f"{key_prefix}_eval_{task['id']}"):
            result = st.radio(
                "Kết quả đánh giá",
                config.EVAL_RESULTS,
                format_func=lambda r: config.EVAL_LABELS[r],
                horizontal=True,
            )
            comment = st.text_area(
                "Nhận xét đánh giá",
                max_chars=config.MAX_TEXT_LENGTH,
                help="Bắt buộc khi đánh giá 'Không đạt'.",
            )
            submitted = st.form_submit_button("Lưu đánh giá", type="primary", width="stretch")
        if submitted and c.run_action(
            task_service.evaluate_task,
            user,
            task["id"],
            result,
            comment,
            success=f"Đã đánh giá công việc #{task['id']}: {config.EVAL_LABELS[result]}.",
            flash_key=flash_key,
        ):
            st.rerun()
    if permissions.can_reopen(user, task):
        with st.form(f"{key_prefix}_reopen_{task['id']}"):
            reason = st.text_area("Lý do mở lại (bắt buộc)", max_chars=config.MAX_PART_LENGTH)
            submitted = st.form_submit_button("Mở lại công việc để làm tiếp", width="stretch")
        if submitted and c.run_action(
            task_service.reopen_task,
            user,
            task["id"],
            reason,
            success=f"Đã mở lại công việc #{task['id']}.",
            flash_key=flash_key,
        ):
            st.rerun()


def render_outcome(task: dict) -> None:
    """Báo cáo hoàn thành và kết quả đánh giá (nếu có)."""
    if task.get("report_summary"):
        st.markdown(f"**Báo cáo hoàn thành của chủ trì:** {task['report_summary']}")
    if task["status"] == config.STATUS_REPORTED:
        st.info("Công việc đã báo cáo hoàn thành, đang chờ trưởng phòng đánh giá.")
    elif task.get("eval_result"):
        text = f"Kết quả đánh giá: **{config.EVAL_LABELS[task['eval_result']]}**"
        if task.get("eval_comment"):
            text += f" - {task['eval_comment']}"
        (st.success if task["eval_result"] == config.EVAL_PASS else st.error)(text)


def render_summary(task: dict) -> None:
    """Khối thông tin ngắn gọn, dùng cả ở trang Phê duyệt."""
    st.markdown(
        f"**Loại:** {c.type_label(task['task_type'])}  \n"
        f"**Trạng thái:** {c.status_label(task['status'])}  \n"
        f"**Hạn hoàn thành:** {c.fmt_date(task['deadline'])}"
        + ("  ⚠ **quá hạn**" if task.get("overdue") else "")
        + f"  \n**Người đề xuất:** {task['proposer_name']}"
        + (
            f"  \n**{'Người thực hiện (chủ trì)' if task['task_type'] in config.SINGLE_ASSIGNEE_TASK_TYPES else 'Chủ trì'}:** "
            f"{task['lead_name']}"
            if task.get("lead_name")
            else ""
        )
        + (f"  \n**Người phê duyệt:** {task['approver_name']}" if task.get("approver_name") else "")
    )
    if task.get("description"):
        st.write(task["description"])
    if task["status"] == config.STATUS_REJECTED and task.get("reject_reason"):
        st.warning(f"Bị từ chối: {task['reject_reason']}")


def _info_tab(user: dict, task: dict) -> None:
    render_summary(task)
    render_outcome(task)

    if permissions.can_report_completion(user, task):
        st.divider()
        st.caption(
            "Báo cáo hoàn thành khi mọi thành viên đã đạt 100% và có báo cáo kết quả. "
            "Sau khi báo cáo, công việc chuyển sang chờ trưởng phòng đánh giá."
        )
        with st.form(f"report_{task['id']}"):
            summary = st.text_area("Báo cáo tổng hợp", max_chars=config.MAX_TEXT_LENGTH)
            submitted = st.form_submit_button("Báo cáo hoàn thành", type="primary", width="stretch")
        if submitted and c.run_action(
            task_service.report_completion,
            user,
            task["id"],
            summary,
            success="Đã báo cáo hoàn thành, chờ trưởng phòng đánh giá.",
            flash_key=FLASH_KEY,
        ):
            st.rerun()

    if permissions.can_evaluate(user, task) or permissions.can_reopen(user, task):
        st.divider()
        render_evaluation_controls(user, task, "detail")

    if permissions.can_submit_task(user, task):
        st.divider()
        st.caption(
            "Gửi phê duyệt cần có người chủ trì và ít nhất một phần việc được phân công."
        )
        if st.button("Gửi phê duyệt", key=f"submit_{task['id']}", type="primary", width="stretch"):
            if c.run_action(
                task_service.submit_task,
                user,
                task["id"],
                success="Đã gửi phê duyệt.",
                flash_key=FLASH_KEY,
            ):
                st.rerun()

    if permissions.can_edit_task(user, task):
        with st.expander("Sửa thông tin công việc"):
            _edit_form(user, task)


def _edit_form(user: dict, task: dict) -> None:
    people = task_service.list_assignable_users()
    with st.form(f"edit_task_{task['id']}"):
        title = st.text_input("Tên công việc", value=task["title"], max_chars=config.MAX_TITLE_LENGTH)
        description = st.text_area(
            "Mô tả", value=task["description"] or "", max_chars=config.MAX_TEXT_LENGTH
        )
        deadline = st.date_input("Hạn hoàn thành", value=date.fromisoformat(task["deadline"]))
        lead_id = None
        if task["task_type"] == config.TYPE_TEAM:
            ids = [p["id"] for p in people]
            index = ids.index(task["lead_id"]) if task["lead_id"] in ids else 0
            lead_id = st.selectbox(
                "Người chủ trì",
                ids,
                index=index,
                format_func=lambda i: next(p["full_name"] for p in people if p["id"] == i),
            )
        saved = st.form_submit_button("Lưu thay đổi", width="stretch")
    if saved:
        try:
            changed = task_service.update_task(
                user, task["id"], title=title, description=description, deadline=deadline, lead_id=lead_id
            )
        except (ValueError, permissions.PermissionDenied) as exc:
            st.error(str(exc))
        else:
            c.flash(FLASH_KEY, "Đã lưu thay đổi." if changed else "Không có thay đổi nào.")
            st.rerun()


def _add_single_form(user: dict, task: dict, available: list, names: dict) -> None:
    with st.form(f"add_member_{task['id']}", clear_on_submit=True):
        user_id = st.selectbox("Thành viên", [p["id"] for p in available], format_func=lambda i: names[i])
        content = st.text_area("Nội dung phần việc", max_chars=config.MAX_PART_LENGTH)
        quantity = st.number_input("Khối lượng", min_value=0.0, value=0.0, step=1.0)
        unit = st.text_input(
            "Đơn vị", placeholder="ví dụ: báo cáo, giờ, văn bản", max_chars=config.MAX_UNIT_LENGTH
        )
        added = st.form_submit_button("Thêm phần việc", width="stretch")
    if added:
        if c.run_action(
            task_service.add_member,
            user,
            task["id"],
            user_id,
            content,
            quantity or None,
            unit,
            success="Đã thêm phần việc.",
            flash_key=FLASH_KEY,
        ):
            st.rerun()


def _add_bulk_form(user: dict, task: dict, available: list) -> None:
    """Một loại việc cho nhiều người: nhập nội dung/đơn vị một lần, khối lượng từng người trong bảng."""
    st.caption(
        "Nhập nội dung và đơn vị một lần. Trong bảng, tích chọn người nhận và điền khối lượng của từng người. "
        "Mỗi người vẫn là một phần việc riêng; có thể sửa riêng sau."
    )
    preselect = st.checkbox("Chọn sẵn tất cả mọi người", key=f"bulk_all_{task['id']}")
    table = pd.DataFrame(
        {
            "Chọn": [preselect] * len(available),
            "Thành viên": [p["full_name"] for p in available],
            "Khối lượng": [None] * len(available),
        }
    )
    with st.form(f"bulk_member_{task['id']}"):
        content = st.text_area("Nội dung phần việc (chung)", max_chars=config.MAX_PART_LENGTH)
        unit = st.text_input(
            "Đơn vị (chung)", placeholder="ví dụ: báo cáo, giờ, văn bản", max_chars=config.MAX_UNIT_LENGTH
        )
        edited = st.data_editor(
            table,
            key=f"bulk_table_{task['id']}_{int(preselect)}",
            hide_index=True,
            width="stretch",
            disabled=["Thành viên"],
            column_config={
                "Chọn": st.column_config.CheckboxColumn("Chọn", default=False),
                "Khối lượng": st.column_config.NumberColumn("Khối lượng", min_value=0.0, step=1.0),
            },
        )
        submitted = st.form_submit_button("Thêm các phần việc đã chọn", type="primary", width="stretch")
    if not submitted:
        return

    rows = []
    for index, row in edited.iterrows():
        if not bool(row["Chọn"]):
            continue
        quantity = row["Khối lượng"]
        rows.append(
            {
                "user_id": available[index]["id"],
                "quantity": None if pd.isna(quantity) else float(quantity),
            }
        )
    if not rows:
        st.error("Hãy tích chọn ít nhất một thành viên trong bảng.")
        return
    try:
        task_service.add_members_bulk(user, task["id"], content, unit, rows)
    except (ValueError, permissions.PermissionDenied) as exc:
        st.error(str(exc))
        return
    c.flash(FLASH_KEY, f"Đã thêm {len(rows)} phần việc.")
    st.rerun()



def _comment_form(user: dict, task: dict) -> None:
    by_id = {m["id"]: m for m in task["members"]}
    with st.expander("Nhận xét phần việc của thành viên"):
        member_id = st.selectbox(
            "Chọn thành viên",
            list(by_id),
            format_func=lambda i: by_id[i]["full_name"],
            key=f"comment_pick_{task['id']}",
        )
        member = by_id[member_id]
        with st.form(f"comment_{task['id']}_{member_id}"):
            comment = st.text_area(
                "Nhận xét", value=member["lead_comment"] or "", max_chars=config.MAX_PART_LENGTH
            )
            saved = st.form_submit_button("Lưu nhận xét", width="stretch")
        if saved:
            try:
                changed = task_service.comment_member(user, member_id, comment)
            except (ValueError, permissions.PermissionDenied) as exc:
                st.error(str(exc))
            else:
                c.flash(FLASH_KEY, "Đã lưu nhận xét." if changed else "Không có thay đổi nào.")
                st.rerun()


def _allocation_tab(user: dict, task: dict) -> None:
    _members_table(task)

    if task["task_type"] == config.TYPE_TEAM:
        updatable = [
            member for member in task["members"]
            if permissions.can_update_progress(user, task, member)
        ]
        if updatable:
            with st.expander("Cập nhật tiến độ thành viên", expanded=True):
                for member in updatable:
                    st.markdown(f"**{member['full_name']} · {member['content']}**")
                    render_progress_form(user, task, member, "detail")
    else:
        mine = next((m for m in task["members"] if m["user_id"] == user["id"]), None)
        if mine and permissions.can_update_progress(user, task, mine):
            with st.expander("Cập nhật tiến độ phần việc của tôi", expanded=True):
                render_progress_form(user, task, mine, "detail")
    if task["members"] and permissions.can_comment_member(user, task):
        _comment_form(user, task)

    if not permissions.can_edit_allocation(user, task):
        return

    people = task_service.list_assignable_users()
    assigned = {m["user_id"] for m in task["members"]}
    available = [p for p in people if p["id"] not in assigned]
    names = {p["id"]: p["full_name"] for p in people}

    single_assignee = task["task_type"] in config.SINGLE_ASSIGNEE_TASK_TYPES
    with st.expander("Thêm phần việc") if not single_assignee else st.container():
        if single_assignee:
            caption = (
                "Công việc giao trực tiếp chỉ có một người thực hiện nên không thêm hoặc xóa phần việc."
                if task["task_type"] == config.TYPE_DIRECT
                else "Công việc cá nhân chỉ có một người thực hiện nên không thêm hoặc xóa phần việc."
            )
            st.caption(caption)
        elif not available:
            st.caption("Mọi người đều đã có phần việc trong công việc này.")
        else:
            mode = st.radio(
                "Cách nhập",
                ["Từng người", "Nhập bảng (nhiều người)"],
                horizontal=True,
                key=f"add_mode_{task['id']}",
            )
            if mode == "Từng người":
                _add_single_form(user, task, available, names)
            else:
                _add_bulk_form(user, task, available)

    if task["members"]:
        with st.expander("Sửa hoặc xóa phần việc"):
            by_id = {m["id"]: m for m in task["members"]}
            member_id = st.selectbox(
                "Chọn phần việc",
                list(by_id),
                format_func=lambda i: f"{by_id[i]['full_name']} - {by_id[i]['content'][:40]}",
                key=f"member_pick_{task['id']}",
            )
            member = by_id[member_id]
            with st.form(f"edit_member_{task['id']}_{member_id}"):
                content = st.text_area(
                    "Nội dung phần việc", value=member["content"], max_chars=config.MAX_PART_LENGTH
                )
                quantity = st.number_input(
                    "Khối lượng", min_value=0.0, value=float(member["quantity"] or 0.0), step=1.0
                )
                unit = st.text_input("Đơn vị", value=member["unit"] or "", max_chars=config.MAX_UNIT_LENGTH)
                saved = st.form_submit_button("Lưu phần việc", width="stretch")
            if saved:
                try:
                    # Khối lượng 0 nghĩa là không đặt khối lượng.
                    changed = task_service.update_member(
                        user, member_id, content, quantity or None, unit
                    )
                except (ValueError, permissions.PermissionDenied) as exc:
                    st.error(str(exc))
                else:
                    c.flash(FLASH_KEY, "Đã lưu phần việc." if changed else "Không có thay đổi nào.")
                    st.rerun()

            if single_assignee:
                return
            confirm = st.checkbox("Xác nhận xóa phần việc này", key=f"confirm_remove_{task['id']}_{member_id}")
            if st.button("Xóa phần việc", key=f"remove_{task['id']}_{member_id}", disabled=not confirm, width="stretch"):
                if c.run_action(
                    task_service.remove_member,
                    user,
                    member_id,
                    success="Đã xóa phần việc.",
                    flash_key=FLASH_KEY,
                ):
                    st.rerun()


def _log_tab(task: dict) -> None:
    logs = log_service.get_logs(task["id"])
    if not logs:
        st.caption("Chưa có nhật ký.")
        return
    df = pd.DataFrame(
        [
            {
                "Thời gian": to_local_str(log["created_at"]),
                "Người thực hiện": log["actor_name"],
                "Hành động": config.LOG_LABELS.get(log["action"], log["action"]),
                "Chi tiết": c.describe_log(log["action"], log["detail"]),
            }
            for log in reversed(logs)
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)


def render_detail(user: dict, task_id: int) -> None:
    task = task_service.get_task(task_id)
    if task is None:
        st.error("Không tìm thấy công việc.")
        return
    st.subheader(f"#{task['id']} · {task['title']}")
    info_tab, allocation_tab, log_tab = st.tabs(["Thông tin", "Phần việc", "Nhật ký"])
    with info_tab:
        _info_tab(user, task)
    with allocation_tab:
        _allocation_tab(user, task)
    with log_tab:
        _log_tab(task)
