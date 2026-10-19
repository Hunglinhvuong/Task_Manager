"""Tổng quan: các chỉ số chính, phân bố theo trạng thái, việc quá hạn/sắp đến hạn, tải công việc từng người."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config
from core import permissions, report_service, task_service
from core.timeutil import today_vn
from ui import components as c

# Một màu cho biểu đồ một chuỗi số liệu (màu chủ đạo, đã kiểm tra tương phản trên nền sáng và tối).
_BAR_COLOR = "#2a78d6"


def _days_late(deadline_iso: str, today: date) -> int:
    return (today - date.fromisoformat(deadline_iso)).days


def _task_table(rows: list, *, late: bool) -> None:
    if not rows:
        st.caption("Không có công việc nào.")
        return
    today = today_vn()
    data = []
    for t in rows:
        item = {
            "Mã": t["id"],
            "Tên công việc": t["title"],
            "Loại": c.type_label(t["task_type"]),
            "Trạng thái": c.status_label(t["status"]),
            "Chủ trì": t["lead_name"] or "",
            "Hạn": c.fmt_date(t["deadline"]),
        }
        if late:
            item["Trễ (ngày)"] = _days_late(t["deadline"], today)
        else:
            item["Còn (ngày)"] = (date.fromisoformat(t["deadline"]) - today).days
        item["Tiến độ TB"] = round(t["avg_progress"]) if t["avg_progress"] is not None else None
        data.append(item)
    st.dataframe(
        pd.DataFrame(data),
        width="stretch",
        hide_index=True,
        column_config={
            "Tiến độ TB": st.column_config.ProgressColumn("Tiến độ TB", min_value=0, max_value=100, format="%d%%")
        },
    )


def _workload_table(rows: list) -> None:
    if not rows:
        st.caption("Chưa có người dùng nào.")
        return
    df = pd.DataFrame(
        [
            {
                "Họ tên": r["full_name"],
                "Phần việc đang làm": r["active_parts"],
                "Đã xong (100%)": r["done_parts"],
                "Tiến độ TB": round(r["avg_progress"]) if r["avg_progress"] is not None else None,
                "Quá hạn": r["overdue_parts"],
                "Đang chủ trì": r["leading"],
            }
            for r in rows
        ]
    )
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "Tiến độ TB": st.column_config.ProgressColumn("Tiến độ TB", min_value=0, max_value=100, format="%d%%")
        },
    )


def _status_chart(counts: dict) -> None:
    df = pd.DataFrame(
        {
            "Trạng thái": [c.status_label(s) for s in config.STATUSES],
            "Số công việc": [counts[s] for s in config.STATUSES],
        }
    )
    st.markdown("**Số công việc theo trạng thái**")
    if df["Số công việc"].sum() == 0:
        st.caption("Chưa có công việc nào.")
        return
    # Thứ tự trạng thái theo quy trình (không xếp theo bảng chữ cái), một màu, không cần chú giải.
    st.bar_chart(df, x="Trạng thái", y="Số công việc", horizontal=True, color=_BAR_COLOR, sort=False)
    with st.expander("Xem dạng bảng"):
        st.dataframe(df, width="stretch", hide_index=True)


def render(user: dict) -> None:
    st.header("Tổng quan")
    data = report_service.overview()

    # Hàng chỉ số chính
    cols = st.columns(5)
    cols[0].metric("Đang thực hiện", data["in_progress"])
    cols[1].metric("⚠ Quá hạn", data["overdue_count"])
    cols[2].metric("Chờ phê duyệt", data["pending_approvals"])
    cols[3].metric("Chờ đánh giá", data["pending_evaluations"])
    cols[4].metric("Đạt / Không đạt", f"{data['passed']} / {data['failed']}")

    # Việc cần bạn xử lý
    notes = []
    if permissions.is_manager(user):
        if data["pending_approvals"]:
            notes.append(f"{data['pending_approvals']} công việc chờ bạn phê duyệt")
        if data["pending_evaluations"]:
            notes.append(f"{data['pending_evaluations']} công việc chờ bạn đánh giá")
    mine = task_service.list_my_work(user["id"])
    if mine["parts"]:
        late = sum(1 for p in mine["parts"] if p["overdue"])
        text = f"{len(mine['parts'])} phần việc của bạn đang thực hiện"
        notes.append(text + (f", trong đó {late} quá hạn" if late else ""))
    if mine["leading"]:
        notes.append(f"bạn đang chủ trì {len(mine['leading'])} công việc")
    if notes:
        st.info("Cần chú ý: " + "; ".join(notes) + ".")

    left, right = st.columns([1, 1])
    with left:
        _status_chart(data["status_counts"])
    with right:
        st.markdown("**Tiến độ trung bình các việc đang thực hiện**")
        avg = data["avg_progress_in_progress"]
        if avg is None:
            st.caption("Chưa có phần việc nào đang thực hiện.")
        else:
            st.progress(round(avg) / 100, text=f"{round(avg)}%")
        st.caption(f"Tổng số công việc: {data['total']}")

    tab_late, tab_soon, tab_people = st.tabs(
        [
            f"⚠ Quá hạn ({data['overdue_count']})",
            f"Sắp đến hạn 7 ngày ({len(data['due_soon'])})",
            "Tải công việc theo người",
        ]
    )
    with tab_late:
        _task_table(data["overdue"], late=True)
    with tab_soon:
        st.caption("Chỉ tính công việc đang thực hiện có hạn trong 7 ngày tới (kể cả hôm nay).")
        _task_table(data["due_soon"], late=False)
    with tab_people:
        st.caption("Phần việc của công việc đang thực hiện. Quá hạn = phần việc của công việc đã quá hạn.")
        _workload_table(data["workload"])
