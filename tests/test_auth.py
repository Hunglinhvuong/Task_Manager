import pytest

import config
from core import auth, database, permissions

PASSWORD = "MatKhau@123"


def _admin(username="admin"):
    uid = auth.bootstrap_user(username, "Quản trị", PASSWORD, config.ROLE_ADMIN)
    return auth.get_user(uid)


def _staff(username="nv01"):
    uid = auth.bootstrap_user(username, "Nhân viên", PASSWORD, config.ROLE_STAFF)
    return auth.get_user(uid)


# --- Băm mật khẩu ------------------------------------------------------
def test_hash_is_salted_and_not_plaintext():
    h1 = auth.hash_password(PASSWORD)
    h2 = auth.hash_password(PASSWORD)
    assert h1 != h2
    assert PASSWORD not in h1
    assert auth.verify_password(PASSWORD, h1)
    assert auth.verify_password(PASSWORD, h2)
    assert not auth.verify_password("sai-mat-khau", h1)


def test_verify_rejects_malformed_hash():
    assert not auth.verify_password(PASSWORD, "khong-phai-hash")
    assert not auth.verify_password(PASSWORD, "bcrypt$1$2$3$4$5")
    assert not auth.verify_password(PASSWORD, "")


def test_password_stored_hashed_in_database(db_path):
    _staff()
    with database.connect() as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored != PASSWORD
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")


def test_user_dicts_never_expose_hash(db_path):
    user = _staff()
    assert "password_hash" not in user
    assert all("password_hash" not in u for u in auth.list_users())
    assert "password_hash" not in auth.authenticate("nv01", PASSWORD)


# --- Đăng nhập ---------------------------------------------------------
def test_authenticate_success_and_failures(db_path):
    _staff("nv01")
    assert auth.authenticate("nv01", PASSWORD)["username"] == "nv01"
    assert auth.authenticate("  NV01 ", PASSWORD) is not None  # không phân biệt hoa thường
    assert auth.authenticate("nv01", "sai") is None
    assert auth.authenticate("khong_co", PASSWORD) is None
    assert auth.authenticate("", "") is None
    assert auth.authenticate(None, None) is None


def test_inactive_user_cannot_login(db_path):
    admin = _admin()
    staff = _staff()
    auth.set_active(admin, staff["id"], False)
    assert auth.authenticate("nv01", PASSWORD) is None
    auth.set_active(admin, staff["id"], True)
    assert auth.authenticate("nv01", PASSWORD) is not None


# --- Kiểm tra dữ liệu --------------------------------------------------
def test_weak_password_rejected(db_path):
    with pytest.raises(ValueError):
        auth.bootstrap_user("nv01", "A", "ngan", config.ROLE_STAFF)


@pytest.mark.parametrize("bad", ["ab", "Có Dấu", "viet hoa", "a" * 40, "", "x@y"])
def test_bad_username_rejected(db_path, bad):
    with pytest.raises(ValueError):
        auth.bootstrap_user(bad, "A", PASSWORD, config.ROLE_STAFF)


def test_duplicate_username_rejected_case_insensitive(db_path):
    _staff("nv01")
    with pytest.raises(ValueError):
        auth.bootstrap_user("NV01", "Khác", PASSWORD, config.ROLE_STAFF)


def test_invalid_role_and_empty_name_rejected(db_path):
    with pytest.raises(ValueError):
        auth.bootstrap_user("nv01", "A", PASSWORD, "BOSS")
    with pytest.raises(ValueError):
        auth.bootstrap_user("nv01", "   ", PASSWORD, config.ROLE_STAFF)


# --- Phân quyền quản lý tài khoản -------------------------------------
@pytest.mark.parametrize("role", [config.ROLE_MANAGER, config.ROLE_STAFF])
def test_non_admin_cannot_manage_accounts(db_path, role):
    _admin()
    uid = auth.bootstrap_user("khac", "Người khác", PASSWORD, role)
    actor = auth.get_user(uid)
    target = _staff("nv01")
    with pytest.raises(permissions.PermissionDenied):
        auth.create_user(actor, "moi", "Mới", PASSWORD, config.ROLE_STAFF)
    with pytest.raises(permissions.PermissionDenied):
        auth.reset_password(actor, target["id"], "MatKhauMoi@1")
    with pytest.raises(permissions.PermissionDenied):
        auth.set_active(actor, target["id"], False)
    with pytest.raises(permissions.PermissionDenied):
        auth.set_role(actor, target["id"], config.ROLE_MANAGER)
    assert auth.get_user_by_username("moi") is None


def test_inactive_admin_loses_permission(db_path):
    admin1 = _admin("admin1")
    admin2 = _admin("admin2")
    auth.set_active(admin1, admin2["id"], False)
    stale_actor = auth.get_user(admin2["id"])  # is_active = False
    with pytest.raises(permissions.PermissionDenied):
        auth.create_user(stale_actor, "moi", "Mới", PASSWORD, config.ROLE_STAFF)


def test_admin_can_create_and_reset_password(db_path):
    admin = _admin()
    uid = auth.create_user(admin, "nv02", "Nhân viên 2", PASSWORD, config.ROLE_STAFF)
    assert auth.authenticate("nv02", PASSWORD)["id"] == uid

    auth.reset_password(admin, uid, "MatKhauMoi@1")
    assert auth.authenticate("nv02", PASSWORD) is None
    assert auth.authenticate("nv02", "MatKhauMoi@1") is not None

    with pytest.raises(ValueError):
        auth.reset_password(admin, uid, "ngan")


def test_user_can_change_own_password_after_verifying_current_password(db_path):
    user = _staff()

    auth.change_password(user, PASSWORD, "MatKhauMoi@1")

    assert auth.authenticate("nv01", PASSWORD) is None
    assert auth.authenticate("nv01", "MatKhauMoi@1") is not None


def test_change_password_rejects_wrong_current_or_weak_new_password(db_path):
    user = _staff()

    with pytest.raises(ValueError, match="Mật khẩu hiện tại không đúng"):
        auth.change_password(user, "sai-mat-khau", "MatKhauMoi@1")
    with pytest.raises(ValueError):
        auth.change_password(user, PASSWORD, "ngan")

    assert auth.authenticate("nv01", PASSWORD) is not None


def test_inactive_user_cannot_change_password(db_path):
    admin = _admin()
    user = _staff()
    auth.set_active(admin, user["id"], False)

    with pytest.raises(permissions.PermissionDenied):
        auth.change_password(user, PASSWORD, "MatKhauMoi@1")

    assert auth.authenticate("nv01", PASSWORD) is None


def test_admin_cannot_lock_self(db_path):
    admin = _admin()
    _admin("admin2")
    with pytest.raises(ValueError):
        auth.set_active(admin, admin["id"], False)


def test_cannot_remove_last_active_admin(db_path):
    admin1 = _admin("admin1")
    with pytest.raises(ValueError):
        auth.set_role(admin1, admin1["id"], config.ROLE_STAFF)

    admin2 = _admin("admin2")
    auth.set_active(admin1, admin2["id"], False)  # admin2 bị khóa -> admin1 là admin cuối
    with pytest.raises(ValueError):
        auth.set_role(admin1, admin1["id"], config.ROLE_MANAGER)

    auth.set_active(admin1, admin2["id"], True)
    auth.set_role(admin1, admin1["id"], config.ROLE_MANAGER)  # nay còn admin2 nên được phép
    assert auth.get_user(admin1["id"])["role"] == config.ROLE_MANAGER


def test_unknown_target_user(db_path):
    admin = _admin()
    with pytest.raises(ValueError):
        auth.reset_password(admin, 9999, PASSWORD)
    with pytest.raises(ValueError):
        auth.set_active(admin, 9999, False)
    with pytest.raises(ValueError):
        auth.set_role(admin, 9999, config.ROLE_STAFF)
