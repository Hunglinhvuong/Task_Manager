"""Điểm vào ứng dụng Streamlit: đăng nhập và điều hướng.

Chạy:  streamlit run app.py
"""
import streamlit as st

import config
from core import database
from ui import accounts, approvals, change_password, login, my_tasks, overview, reports, task_list

st.set_page_config(page_title=config.APP_TITLE, page_icon="📋", layout="wide")


@st.cache_resource
def _ensure_db(path: str) -> bool:
    database.init_db(path)
    return True


_ensure_db(str(config.get_db_path()))

user = login.current_user()
if user is None:
    login.render_login()
    st.stop()

PAGES = {
    "Tổng quan": overview,
    "Danh sách công việc": task_list,
    "Phê duyệt": approvals,
    "Công việc của tôi": my_tasks,
    "Báo cáo": reports,
    "Đổi mật khẩu": change_password,
}
if user["role"] == config.ROLE_ADMIN:
    PAGES["Tài khoản"] = accounts

with st.sidebar:
    st.subheader(config.APP_TITLE)
    st.write(f"**{user['full_name']}**")
    st.caption(config.ROLE_LABELS.get(user["role"], user["role"]))
    page_name = st.radio("Mục", list(PAGES), label_visibility="collapsed")
    if st.button("Đăng xuất", width="stretch"):
        login.logout()
        st.rerun()

PAGES[page_name].render(user)
