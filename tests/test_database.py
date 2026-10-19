import sqlite3

import pytest

import config
from core import auth, database, log_service


def _user(username="nv01", role=config.ROLE_STAFF):
    return auth.bootstrap_user(username, f"Người dùng {username}", "MatKhau@123", role)


def _task(conn, proposer_id, task_type="DIRECT", lead_id=None):
    cur = conn.execute(
        "INSERT INTO tasks (title, task_type, proposer_id, lead_id) VALUES (?, ?, ?, ?)",
        ("Việc thử", task_type, proposer_id, lead_id),
    )
    return cur.lastrowid


def test_only_four_business_tables(db_path):
    with database.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert names == {"users", "tasks", "task_members", "task_logs"}


def test_init_db_is_idempotent(db_path):
    uid = _user()
    database.init_db()
    database.init_db()
    assert auth.get_user(uid) is not None


def test_foreign_keys_enforced(db_path):
    with database.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            _task(conn, proposer_id=999)


def test_invalid_role_rejected(db_path):
    with database.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES ('x', 'X', 'h', 'BOSS')"
            )


def test_invalid_status_and_type_rejected(db_path):
    uid = _user()
    with database.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (title, task_type, proposer_id, status) VALUES ('t', 'DIRECT', ?, 'DONE')",
                (uid,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (title, task_type, proposer_id) VALUES ('t', 'OTHER', ?)", (uid,)
            )


def test_direct_task_can_have_lead(db_path):
    """Từ schema v4, công việc giao trực tiếp có chủ trì (người được giao)."""
    uid = _user()
    with database.connect() as conn:
        assert _task(conn, uid, task_type="DIRECT", lead_id=uid)
        assert _task(conn, uid, task_type="TEAM", lead_id=uid)


def test_eval_result_only_when_finished(db_path):
    uid = _user()
    with database.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (title, task_type, proposer_id, status, eval_result) "
                "VALUES ('t', 'DIRECT', ?, 'IN_PROGRESS', 'PASS')",
                (uid,),
            )


def test_one_part_per_member_per_task(db_path):
    uid = _user()
    with database.connect() as conn:
        task_id = _task(conn, uid)
        conn.execute(
            "INSERT INTO task_members (task_id, user_id, content) VALUES (?, ?, 'Phần 1')",
            (task_id, uid),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_members (task_id, user_id, content) VALUES (?, ?, 'Phần 2')",
                (task_id, uid),
            )


def test_member_progress_and_quantity_constraints(db_path):
    uid = _user()
    with database.connect() as conn:
        task_id = _task(conn, uid)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_members (task_id, user_id, content, progress) VALUES (?, ?, 'a', 101)",
                (task_id, uid),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_members (task_id, user_id, content, quantity) VALUES (?, ?, 'a', -1)",
                (task_id, uid),
            )


def test_task_logs_are_immutable(db_path):
    uid = _user()
    with database.transaction() as conn:
        task_id = _task(conn, uid)
        log_id = log_service.add_log(conn, task_id, uid, "CREATE", {"title": "Việc thử"})

    with database.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE task_logs SET action = 'HACK' WHERE id = ?", (log_id,))
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM task_logs WHERE id = ?", (log_id,))
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM task_logs")

    logs = log_service.get_logs(task_id)
    assert len(logs) == 1
    assert logs[0]["action"] == "CREATE"
    assert logs[0]["detail"] == {"title": "Việc thử"}


def test_transaction_rolls_back_everything_on_error(db_path):
    uid = _user()
    with pytest.raises(RuntimeError):
        with database.transaction() as conn:
            task_id = _task(conn, uid)
            log_service.add_log(conn, task_id, uid, "CREATE")
            raise RuntimeError("lỗi giữa chừng")

    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0] == 0
