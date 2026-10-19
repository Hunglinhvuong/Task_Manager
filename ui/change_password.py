"""Giao diện tự đổi mật khẩu cho người dùng đang đăng nhập."""
import streamlit as st

import config
from core import auth, permissions


def render(user: dict) -> None:
    st.header("Đổi mật khẩu")
    st.caption(f"Mật khẩu mới cần có ít nhất {config.MIN_PASSWORD_LENGTH} ký tự.")

    with st.form("change_password_form", clear_on_submit=True):
        current_password = st.text_input("Mật khẩu hiện tại", type="password")
        new_password = st.text_input("Mật khẩu mới", type="password")
        confirm_password = st.text_input("Xác nhận mật khẩu mới", type="password")
        submitted = st.form_submit_button("Đổi mật khẩu", width="stretch")

    if submitted:
        if new_password != confirm_password:
            st.error("Mật khẩu mới và xác nhận mật khẩu không khớp.")
            return
        try:
            auth.change_password(user, current_password, new_password)
        except (ValueError, permissions.PermissionDenied) as exc:
            st.error(str(exc))
        else:
            st.success("Đã đổi mật khẩu thành công.")
