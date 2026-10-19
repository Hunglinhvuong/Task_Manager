"""Quản lý tài khoản (chỉ ADMIN)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from core import auth, permissions

_FLASH_KEY = "accounts_flash"


def _flash(message: str) -> None:
    st.session_state[_FLASH_KEY] = message


def _role_label(role: str) -> str:
    return config.ROLE_LABELS.get(role, role)


def render(user: dict) -> None:
    st.header("Quản lý tài khoản")
    if not permissions.can_manage_accounts(user):
        st.error("Chỉ quản trị viên (ADMIN) mới được truy cập mục này.")
        return

    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        st.success(flash)

    users = auth.list_users()
    df = pd.DataFrame(
        [
            {
                "Tên đăng nhập": u["username"],
                "Họ tên": u["full_name"],
                "Vai trò": _role_label(u["role"]),
                "Trạng thái": "Hoạt động" if u["is_active"] else "Đã khóa",
            }
            for u in users
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)

    with st.expander("Thêm tài khoản mới"):
        with st.form("create_user_form", clear_on_submit=True):
            username = st.text_input("Tên đăng nhập (chữ thường không dấu)")
            full_name = st.text_input("Họ tên")
            role = st.selectbox(
                "Vai trò",
                config.ROLES,
                index=config.ROLES.index(config.ROLE_STAFF),
                format_func=_role_label,
            )
            password = st.text_input(
                f"Mật khẩu (tối thiểu {config.MIN_PASSWORD_LENGTH} ký tự)", type="password"
            )
            submitted = st.form_submit_button("Tạo tài khoản", width="stretch")
        if submitted:
            try:
                auth.create_user(user, username, full_name, password, role)
            except (ValueError, permissions.PermissionDenied) as exc:
                st.error(str(exc))
            else:
                _flash(f"Đã tạo tài khoản '{username.strip().lower()}'.")
                st.rerun()

    if not users:
        return

    st.subheader("Cập nhật tài khoản")
    by_id = {u["id"]: u for u in users}
    selected_id = st.selectbox(
        "Chọn tài khoản",
        list(by_id),
        format_func=lambda i: f"{by_id[i]['full_name']} ({by_id[i]['username']})",
    )
    target = by_id[selected_id]

    with st.form("reset_password_form", clear_on_submit=True):
        new_password = st.text_input("Mật khẩu mới", type="password")
        if st.form_submit_button("Đặt lại mật khẩu", width="stretch"):
            try:
                auth.reset_password(user, target["id"], new_password)
            except (ValueError, permissions.PermissionDenied) as exc:
                st.error(str(exc))
            else:
                _flash(f"Đã đặt lại mật khẩu cho '{target['username']}'.")
                st.rerun()

    with st.form("change_role_form"):
        new_role = st.selectbox(
            "Vai trò",
            config.ROLES,
            index=config.ROLES.index(target["role"]),
            format_func=_role_label,
        )
        if st.form_submit_button("Lưu vai trò", width="stretch"):
            try:
                auth.set_role(user, target["id"], new_role)
            except (ValueError, permissions.PermissionDenied) as exc:
                st.error(str(exc))
            else:
                _flash(f"Đã cập nhật vai trò của '{target['username']}'.")
                st.rerun()

    toggle_label = "Khóa tài khoản" if target["is_active"] else "Mở khóa tài khoản"
    if st.button(toggle_label, width="stretch"):
        try:
            auth.set_active(user, target["id"], not target["is_active"])
        except (ValueError, permissions.PermissionDenied) as exc:
            st.error(str(exc))
        else:
            _flash(f"Đã cập nhật trạng thái của '{target['username']}'.")
            st.rerun()
