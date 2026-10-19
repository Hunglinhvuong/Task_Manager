"""Đăng nhập và lấy người dùng hiện tại từ session."""
from __future__ import annotations

from typing import Optional

import streamlit as st

import config
from core import auth

SESSION_KEY = "user"


def current_user() -> Optional[dict]:
    """Người dùng đang đăng nhập, đọc lại từ database ở mỗi lần chạy.

    Nhờ vậy khi ADMIN khóa tài khoản hoặc đổi vai trò, hiệu lực có ngay
    mà không cần chờ người đó đăng xuất.
    """
    data = st.session_state.get(SESSION_KEY)
    if not data:
        return None
    fresh = auth.get_user(data["id"])
    if fresh is None or not fresh["is_active"]:
        st.session_state.pop(SESSION_KEY, None)
        return None
    st.session_state[SESSION_KEY] = fresh
    return fresh


def logout() -> None:
    st.session_state.pop(SESSION_KEY, None)


def render_login() -> None:
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.title(config.APP_TITLE)
        st.caption("Vui lòng đăng nhập để tiếp tục.")
        with st.form("login_form"):
            username = st.text_input("Tên đăng nhập")
            password = st.text_input("Mật khẩu", type="password")
            submitted = st.form_submit_button("Đăng nhập", width="stretch")
        if submitted:
            user = auth.authenticate(username, password)
            if user:
                st.session_state[SESSION_KEY] = user
                st.rerun()
            else:
                st.error("Sai tên đăng nhập hoặc mật khẩu, hoặc tài khoản đã bị khóa.")
