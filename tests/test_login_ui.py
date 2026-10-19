"""Kiểm thử giao diện đăng nhập bằng công cụ AppTest của Streamlit."""
import pytest
from streamlit.testing.v1 import AppTest

import config
from core import auth

APP_FILE = str(config.BASE_DIR / "app.py")
PASSWORD = "MatKhau@123"


def _login(at, username, password):
    at.text_input[0].set_value(username)
    at.text_input[1].set_value(password)
    at.button[0].click()
    return at.run()


def test_login_page_shown_when_not_authenticated(db_path):
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == config.APP_TITLE
    assert "user" not in at.session_state


def test_wrong_password_shows_error(db_path):
    auth.bootstrap_user("nv01", "Nhân viên", PASSWORD, config.ROLE_STAFF)
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at = _login(at, "nv01", "sai-mat-khau")
    assert not at.exception
    assert len(at.error) == 1
    assert "user" not in at.session_state


def test_staff_login_shows_five_sections_without_accounts(db_path):
    auth.bootstrap_user("nv01", "Nhân viên", PASSWORD, config.ROLE_STAFF)
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at = _login(at, "nv01", PASSWORD)
    assert not at.exception
    assert at.session_state["user"]["username"] == "nv01"
    assert list(at.sidebar.radio[0].options) == [
        "Tổng quan",
        "Danh sách công việc",
        "Phê duyệt",
        "Công việc của tôi",
        "Báo cáo",
        "Đổi mật khẩu",
    ]


def test_user_can_change_password_from_sidebar(db_path):
    auth.bootstrap_user("nv01", "Nhân viên", PASSWORD, config.ROLE_STAFF)
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at = _login(at, "nv01", PASSWORD)
    at.sidebar.radio[0].set_value("Đổi mật khẩu").run()

    next(item for item in at.text_input if item.label == "Mật khẩu hiện tại").set_value(PASSWORD)
    next(item for item in at.text_input if item.label == "Mật khẩu mới").set_value("MatKhauMoi@1")
    next(item for item in at.text_input if item.label == "Xác nhận mật khẩu mới").set_value("MatKhauMoi@1")
    at.get("form_submit_button")[0].click().run()

    assert not at.exception
    assert any("Đã đổi mật khẩu thành công" in item.value for item in at.success)
    assert auth.authenticate("nv01", PASSWORD) is None
    assert auth.authenticate("nv01", "MatKhauMoi@1") is not None


def test_admin_sees_accounts_page(db_path):
    auth.bootstrap_user("admin", "Quản trị", PASSWORD, config.ROLE_ADMIN)
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at = _login(at, "admin", PASSWORD)
    assert not at.exception
    assert "Tài khoản" in at.sidebar.radio[0].options
    at.sidebar.radio[0].set_value("Tài khoản").run()
    assert not at.exception
    assert at.header[0].value == "Quản lý tài khoản"


def test_deactivated_user_is_logged_out_on_next_run(db_path):
    admin_id = auth.bootstrap_user("admin", "Quản trị", PASSWORD, config.ROLE_ADMIN)
    staff_id = auth.bootstrap_user("nv01", "Nhân viên", PASSWORD, config.ROLE_STAFF)
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at = _login(at, "nv01", PASSWORD)
    assert "user" in at.session_state

    auth.set_active(auth.get_user(admin_id), staff_id, False)
    at.run()
    assert "user" not in at.session_state
    assert at.title[0].value == config.APP_TITLE  # quay về trang đăng nhập
