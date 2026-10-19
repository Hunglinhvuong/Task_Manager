"""Báo cáo: lọc theo kỳ/trạng thái/loại/người, xem tóm tắt và tải CSV."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

import config
from core import report_service, task_service
from core.timeutil import today_vn
from ui import components as c

_TASK_COLUMNS = [
    ("id", "Mã"),
    ("title", "Tên công việc"),
    ("type_label", "Loại"),
    ("status_label", "Trạng thái"),
    ("proposer_name", "Người đề xuất"),
    ("lead_name", "Chủ trì"),
    ("deadline_text", "Hạn hoàn thành"),
    ("member_count", "Số người"),
    ("progress_text", "Tiến độ TB (%)"),
    ("overdue_text", "Quá hạn"),
    ("eval_text", "Kết quả đánh giá"),
    ("eval_comment", "Nhận xét đánh giá"),
]

_MEMBER_COLUMNS = [
    ("full_name", "Họ tên"),
    ("parts", "Số phần việc"),
    ("completed", "Đã xong (100%)"),
    ("progress_text", "Tiến độ TB (%)"),
    ("passed", "Việc Đạt"),
    ("failed", "Việc Không đạt"),
    ("overdue_parts", "Phần việc quá hạn"),
    ("led_total", "Số việc chủ trì"),
    ("led_passed", "Chủ trì Đạt"),
    ("led_failed", "Chủ trì Không đạt"),
]


def _date_range_controls() -> Tuple[Optional[date], Optional[date]]:
    period = st.selectbox("Kỳ báo cáo (theo hạn hoàn thành)", report_service.PERIODS)
    if period == "Tùy chọn":
        today = today_vn()
        picked = st.date_input("Khoảng ngày", value=(today - timedelta(days=30), today), format="DD/MM/YYYY")
        if isinstance(picked, (tuple, list)):
            if len(picked) == 2:
                return picked[0], picked[1]
            if len(picked) == 1:
                return picked[0], None
            return None, None
        return picked, None
    return report_service.period_range(period)


def _prepare_task_rows(rows: list) -> list:
    prepared = []
    for r in rows:
        prepared.append(
            {
                **r,
                "type_label": c.type_label(r["task_type"]),
                "status_label": c.status_label(r["status"]),
                "deadline_text": c.fmt_date(r["deadline"]),
                "progress_text": round(r["avg_progress"]) if r["avg_progress"] is not None else None,
                "overdue_text": "Có" if r["overdue"] else "",
                "eval_text": config.EVAL_LABELS.get(r["eval_result"], ""),
            }
        )
    return prepared


def _prepare_member_rows(rows: list) -> list:
    return [
        {**r, "progress_text": round(r["avg_progress"]) if r["avg_progress"] is not None else None}
        for r in rows
    ]


def _range_text(date_from: Optional[date], date_to: Optional[date]) -> str:
    if date_from is None and date_to is None:
        return "tất cả thời gian"
    left = date_from.strftime("%d/%m/%Y") if date_from else "..."
    right = date_to.strftime("%d/%m/%Y") if date_to else "..."
    return f"{left} - {right}"


def render(user: dict) -> None:
    st.header("Báo cáo")

    with st.container(border=True):
        date_from, date_to = _date_range_controls()
        col1, col2, col3 = st.columns(3)
        statuses = col1.multiselect("Trạng thái", config.STATUSES, format_func=c.status_label)
        task_type = col2.selectbox(
            "Loại", [None, *config.TASK_TYPES], format_func=lambda t: "Tất cả" if t is None else c.type_label(t)
        )
        people = task_service.list_assignable_users()
        person = col3.selectbox(
            "Người liên quan",
            [None, *[p["id"] for p in people]],
            format_func=lambda i: "Tất cả" if i is None else next(p["full_name"] for p in people if p["id"] == i),
        )

    if date_from and date_to and date_from > date_to:
        st.error("Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.")
        return

    rows = report_service.task_report(
        date_from=date_from,
        date_to=date_to,
        statuses=statuses or None,
        task_type=task_type,
        user_id=person,
    )
    summary = report_service.outcome_summary(rows)
    st.caption(f"Kỳ báo cáo: {_range_text(date_from, date_to)}")

    cols = st.columns(5)
    cols[0].metric("Tổng số công việc", summary["total"])
    cols[1].metric("Đạt", summary["passed"])
    cols[2].metric("Không đạt", summary["failed"])
    cols[3].metric("⚠ Quá hạn", summary["overdue"])
    cols[4].metric(
        "Tỷ lệ đạt",
        "-" if summary["pass_rate"] is None else f"{summary['pass_rate'] * 100:.0f}%",
        help="Đạt / (Đạt + Không đạt), chỉ tính các công việc đã được đánh giá.",
    )

    stamp = today_vn().strftime("%Y%m%d")
    tab_tasks, tab_people = st.tabs(["Danh sách công việc", "Theo người"])

    with tab_tasks:
        if not rows:
            st.info("Không có công việc nào khớp bộ lọc.")
        else:
            prepared = _prepare_task_rows(rows)
            st.dataframe(
                pd.DataFrame([{title: r[key] for key, title in _TASK_COLUMNS} for r in prepared]),
                width="stretch",
                hide_index=True,
            )
            st.download_button(
                "Tải danh sách công việc (CSV)",
                data=report_service.to_csv(_TASK_COLUMNS, prepared),
                file_name=f"bao_cao_cong_viec_{stamp}.csv",
                mime="text/csv",
                width="stretch",
            )

    with tab_people:
        st.caption(
            "Thống kê trên các công việc đã triển khai (đang thực hiện, đã báo cáo, Đạt, Không đạt) có hạn trong kỳ báo cáo. "
            "Không cộng khối lượng giữa các phần việc vì đơn vị khác nhau."
        )
        member_rows = _prepare_member_rows(report_service.member_report(date_from, date_to))
        if not member_rows:
            st.info("Chưa có người dùng nào.")
        else:
            st.dataframe(
                pd.DataFrame([{title: r[key] for key, title in _MEMBER_COLUMNS} for r in member_rows]),
                width="stretch",
                hide_index=True,
            )
            st.download_button(
                "Tải thống kê theo người (CSV)",
                data=report_service.to_csv(_MEMBER_COLUMNS, member_rows),
                file_name=f"bao_cao_theo_nguoi_{stamp}.csv",
                mime="text/csv",
                width="stretch",
            )
