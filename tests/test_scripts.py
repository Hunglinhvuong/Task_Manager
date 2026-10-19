import sqlite3

import config
from core import auth, database
from scripts import backup_db, create_admin, seed_demo


def test_seed_demo_creates_12_staff_and_is_rerunnable(db_path):
    assert seed_demo.main(["--password", "MauThuNghiem@1"]) == 0
    users = auth.list_users()
    staff = [u for u in users if u["role"] == config.ROLE_STAFF]
    managers = [u for u in users if u["role"] == config.ROLE_MANAGER]
    assert len(staff) == 12
    assert len(managers) == 1

    assert seed_demo.main(["--password", "MauThuNghiem@1"]) == 0
    assert len(auth.list_users()) == 13
    assert auth.authenticate("nv01", "MauThuNghiem@1") is not None


def test_create_admin_script(db_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "QuanTri@12345")
    assert create_admin.main(["--username", "quantri"]) == 0
    user = auth.authenticate("quantri", "QuanTri@12345")
    assert user and user["role"] == config.ROLE_ADMIN
    # Tạo trùng thì báo lỗi, không ghi đè
    assert create_admin.main(["--username", "quantri"]) == 1


def test_create_admin_rejects_weak_password(db_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "ngan")
    assert create_admin.main(["--username", "quantri"]) == 1
    assert auth.get_user_by_username("quantri") is None


def test_backup_creates_valid_copy_and_prunes(db_path, tmp_path):
    auth.bootstrap_user("nv01", "Nhân viên", "MatKhau@123", config.ROLE_STAFF)
    dest = tmp_path / "backups"

    first = backup_db.backup(dest, keep=2)
    assert first.exists()
    conn = sqlite3.connect(str(first))
    try:
        assert conn.execute("SELECT username FROM users").fetchone()[0] == "nv01"
    finally:
        conn.close()

    # Tạo thêm các bản sao lưu với tên khác nhau để kiểm tra việc chỉ giữ N bản mới nhất
    for stamp in ("20200101_000000", "20200102_000000", "20200103_000000"):
        (dest / f"app_{stamp}.db").write_bytes(first.read_bytes())
    backup_db.backup(dest, keep=2)
    remaining = sorted(p.name for p in dest.glob("app_*.db"))
    assert len(remaining) == 2
    assert "app_20200101_000000.db" not in remaining


def test_backup_missing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_DB_PATH", str(tmp_path / "khong_co.db"))
    assert backup_db.main(["--dest", str(tmp_path / "b")]) == 1
