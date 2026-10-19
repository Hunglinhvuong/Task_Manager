"""Nâng cấp database Giai đoạn 1 (schema v1) lên v2 mà không mất dữ liệu."""
import sqlite3

import pytest

import config
from core import auth, database, log_service, task_service as ts

OLD_TASK_LOGS = """
CREATE TABLE task_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks (id),
    member_id  INTEGER REFERENCES task_members (id),
    actor_id   INTEGER NOT NULL REFERENCES users (id),
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""


def _make_v1_database(path):
    """Dựng database đúng như Giai đoạn 1: task_logs có khóa ngoại member_id, chưa đặt user_version."""
    database.init_db(path)
    conn = database.get_connection(path)
    try:
        conn.execute("DROP TRIGGER trg_task_logs_no_update")
        conn.execute("DROP TRIGGER trg_task_logs_no_delete")
        conn.execute("DROP INDEX idx_logs_task_created")
        conn.execute("DROP TABLE task_logs")
        conn.execute(OLD_TASK_LOGS)
        conn.execute("CREATE INDEX idx_logs_task_created ON task_logs (task_id, created_at)")
        conn.execute(
            "CREATE TRIGGER trg_task_logs_no_update BEFORE UPDATE ON task_logs "
            "BEGIN SELECT RAISE(ABORT, 'bất biến'); END"
        )
        conn.execute(
            "CREATE TRIGGER trg_task_logs_no_delete BEFORE DELETE ON task_logs "
            "BEGIN SELECT RAISE(ABORT, 'bất biến'); END"
        )
        conn.execute("PRAGMA user_version = 0")
        assert any(r["table"] == "task_members" for r in conn.execute("PRAGMA foreign_key_list(task_logs)"))
    finally:
        conn.close()


def test_upgrade_keeps_data_and_drops_member_fk(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    _make_v1_database(path)

    uid = auth.bootstrap_user("nv01", "Nhân viên", "MatKhau@123", config.ROLE_STAFF)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO tasks (title, task_type, proposer_id) VALUES ('Việc cũ', 'DIRECT', ?)", (uid,)
        )
        log_service.add_log(conn, 1, uid, "TASK_CREATED", {"title": "Việc cũ"})
        log_service.add_log(conn, 1, uid, "GHI_CHU")

    database.init_db()  # nâng cấp

    conn = database.get_connection(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert not any(r["table"] == "task_members" for r in conn.execute("PRAGMA foreign_key_list(task_logs)"))
        assert conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0] == 2
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE task_logs SET action = 'X'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM task_logs")
        # Bảng cũ đã được dọn
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'task_logs_old'"
        ).fetchone() is None
    finally:
        conn.close()

    logs = log_service.get_logs(1)
    assert [log["action"] for log in logs] == ["TASK_CREATED", "GHI_CHU"]
    assert logs[0]["detail"] == {"title": "Việc cũ"}

    # Id tiếp theo vẫn nối tiếp, chạy lại init_db không làm gì thêm
    database.init_db()
    with database.transaction() as conn:
        new_id = log_service.add_log(conn, 1, uid, "TIEP_THEO")
    assert new_id == 3


def test_removing_member_works_after_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    _make_v1_database(path)
    database.init_db()

    manager = auth.get_user(auth.bootstrap_user("tpx", "Trưởng phòng", "MatKhau@123", config.ROLE_MANAGER))
    staff = auth.get_user(auth.bootstrap_user("nv01", "Nhân viên", "MatKhau@123", config.ROLE_STAFF))
    from datetime import timedelta
    from core.timeutil import today_vn

    task_id = ts.create_task(
        manager,
        config.TYPE_DIRECT,
        "Việc",
        None,
        (today_vn() + timedelta(days=3)).isoformat(),
        lead_id=staff["id"],
        members=[{"content": "Phần 1"}],
    )
    member_id = ts.get_task(task_id)["members"][0]["id"]
    with pytest.raises(ValueError):  # việc giao trực tiếp chỉ có một người, không xóa phần việc
        ts.remove_member(manager, member_id)
    # Việc nhóm vẫn xóa được phần việc sau khi nâng cấp
    team_id = ts.create_task(manager, config.TYPE_TEAM, "Việc nhóm", None,
                             (today_vn() + timedelta(days=3)).isoformat(), lead_id=manager["id"])
    team_member = ts.add_member(manager, team_id, staff["id"], "Phần 1")
    ts.remove_member(manager, team_member)
    assert ts.get_task(team_id)["members"] == []


def test_fresh_database_gets_current_version(tmp_path, monkeypatch):
    path = tmp_path / "new.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    database.init_db()
    conn = database.get_connection(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert not any(r["table"] == "task_members" for r in conn.execute("PRAGMA foreign_key_list(task_logs)"))
    finally:
        conn.close()


def _make_v2_database(path):
    """Dựng database đúng như trước khi có done_quantity: task_members chưa có cột này."""
    database.init_db(path)
    conn = database.get_connection(path)
    try:
        conn.execute("ALTER TABLE task_members DROP COLUMN done_quantity")
        conn.execute("PRAGMA user_version = 2")
        assert "done_quantity" not in {r["name"] for r in conn.execute("PRAGMA table_info(task_members)")}
    finally:
        conn.close()


def test_upgrade_v2_to_v3_converts_percent_to_done_quantity(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    _make_v2_database(path)

    conn = database.get_connection(path)
    try:
        conn.execute(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES ('nv01', 'A', 'h', 'STAFF')"
        )
        conn.execute("INSERT INTO tasks (title, task_type, proposer_id) VALUES ('Việc cũ', 'DIRECT', 1)")
        conn.execute(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES ('nv02', 'B', 'h', 'STAFF')"
        )
        conn.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES ('nv03', 'C', 'h', 'STAFF')")
        conn.execute(
            "INSERT INTO task_members (task_id, user_id, content, quantity, unit, progress) VALUES (1, 1, 'A', 50, 'trạm', 40)"
        )
        conn.execute(
            "INSERT INTO task_members (task_id, user_id, content, quantity, unit, progress) VALUES (1, 2, 'B', 8, 'bản', 100)"
        )
        conn.execute(
            "INSERT INTO task_members (task_id, user_id, content, quantity, unit, progress) VALUES (1, 3, 'C', NULL, NULL, 70)"
        )
    finally:
        conn.close()

    database.init_db()  # nâng cấp

    conn = database.get_connection(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        rows = {r["content"]: r for r in conn.execute("SELECT * FROM task_members")}
        assert (rows["A"]["done_quantity"], rows["A"]["progress"]) == (20.0, 40)  # 40% của 50
        assert (rows["B"]["done_quantity"], rows["B"]["progress"]) == (8.0, 100)
        assert (rows["C"]["done_quantity"], rows["C"]["progress"]) == (None, 70)  # không có khối lượng: giữ %
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE task_members SET done_quantity = -1 WHERE id = 1")
    finally:
        conn.close()

    # Chạy lại không làm gì thêm, và nghiệp vụ tính % vẫn đúng trên dữ liệu đã chuyển
    database.init_db()
    assert ts.compute_progress(20.0, 50) == 40


def test_upgrade_from_v1_goes_all_the_way_to_latest(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    _make_v1_database(path)
    conn = database.get_connection(path)
    try:
        conn.execute("ALTER TABLE task_members DROP COLUMN done_quantity")
    finally:
        conn.close()
    database.init_db()
    conn = database.get_connection(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert "done_quantity" in {r["name"] for r in conn.execute("PRAGMA table_info(task_members)")}
        assert not any(r["table"] == "task_members" for r in conn.execute("PRAGMA foreign_key_list(task_logs)"))
    finally:
        conn.close()


OLD_TASKS_CHECK = "CHECK (task_type = 'TEAM' OR lead_id IS NULL),"


def _make_v3_database(path):
    """Dựng database schema v3: bảng tasks còn ràng buộc 'chỉ công việc nhóm mới có chủ trì'."""
    database.init_db(path)
    conn = database.get_connection(path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        ddl = database._tasks_table_sql("tasks_v3", if_not_exists=False)
        ddl = ddl.replace(
            "CHECK (eval_result IS NULL OR status IN ('PASSED', 'FAILED'))",
            OLD_TASKS_CHECK + " CHECK (eval_result IS NULL OR status IN ('PASSED', 'FAILED'))",
        )
        conn.execute(ddl)
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_v3 RENAME TO tasks")
        conn.execute("PRAGMA user_version = 3")
        assert "lead_id IS NULL" in conn.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
    finally:
        conn.close()


def test_upgrade_v3_to_v4_assigns_lead_for_single_person_direct_tasks(tmp_path, monkeypatch):
    path = tmp_path / "v3.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    _make_v3_database(path)

    conn = database.get_connection(path)
    try:
        for i in (1, 2, 3):
            conn.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, 'h', 'STAFF')",
                (f"nv0{i}", f"Người {i}"),
            )
        # 1: DIRECT một người; 2: DIRECT hai người (dữ liệu cũ); 3: TEAM có chủ trì
        conn.execute("INSERT INTO tasks (title, task_type, status, proposer_id) VALUES ('d1', 'DIRECT', 'IN_PROGRESS', 1)")
        conn.execute("INSERT INTO tasks (title, task_type, status, proposer_id) VALUES ('d2', 'DIRECT', 'IN_PROGRESS', 1)")
        conn.execute("INSERT INTO tasks (title, task_type, status, proposer_id, lead_id) VALUES ('t3', 'TEAM', 'DRAFT', 1, 3)")
        conn.execute("INSERT INTO task_members (task_id, user_id, content) VALUES (1, 2, 'P')")
        conn.execute("INSERT INTO task_members (task_id, user_id, content) VALUES (2, 2, 'P')")
        conn.execute("INSERT INTO task_members (task_id, user_id, content) VALUES (2, 3, 'P')")
        conn.execute("INSERT INTO task_logs (task_id, actor_id, action) VALUES (1, 1, 'TASK_CREATED')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET lead_id = 2 WHERE id = 1")  # ràng buộc cũ còn hiệu lực
    finally:
        conn.close()

    database.init_db()  # nâng cấp

    conn = database.get_connection(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        leads = {r["title"]: r["lead_id"] for r in conn.execute("SELECT title, lead_id FROM tasks")}
        assert leads == {"d1": 2, "d2": None, "t3": 3}
        assert conn.execute("SELECT COUNT(*) FROM task_members").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # Các khóa ngoại của bảng con vẫn trỏ đúng bảng "tasks"
        assert {r["table"] for r in conn.execute("PRAGMA foreign_key_list(task_members)")} == {"tasks", "users"}
        indexes = {r["name"] for r in conn.execute("PRAGMA index_list(tasks)")}
        assert {"idx_tasks_status", "idx_tasks_lead", "idx_tasks_deadline"} <= indexes
        conn.execute("UPDATE tasks SET lead_id = 3 WHERE id = 2")  # DIRECT có chủ trì: nay hợp lệ
    finally:
        conn.close()

    database.init_db()  # chạy lại không làm gì thêm
    assert database.get_connection(path).execute("SELECT lead_id FROM tasks WHERE id = 1").fetchone()[0] == 2


def test_upgrade_v4_adds_personal_task_type_without_losing_data(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    monkeypatch.setenv("TASKS_DB_PATH", str(path))
    task_types = config.TASK_TYPES
    monkeypatch.setattr(config, "TASK_TYPES", (config.TYPE_DIRECT, config.TYPE_TEAM))
    database.init_db(path)

    conn = database.get_connection(path)
    try:
        conn.execute(
            "INSERT INTO users (username, full_name, password_hash, role) "
            "VALUES ('nv01', 'Nhân viên', 'hash', 'STAFF')"
        )
        conn.execute(
            "INSERT INTO tasks (title, task_type, status, proposer_id, lead_id) "
            "VALUES ('Việc cũ', 'TEAM', 'DRAFT', 1, 1)"
        )
        conn.execute(
            "INSERT INTO task_members (task_id, user_id, content) VALUES (1, 1, 'Phần cũ')"
        )
        conn.execute("INSERT INTO task_logs (task_id, actor_id, action) VALUES (1, 1, 'TASK_CREATED')")
        conn.execute("PRAGMA user_version = 4")
    finally:
        conn.close()
    monkeypatch.setattr(config, "TASK_TYPES", task_types)

    database.init_db(path)
    conn = database.get_connection(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert tuple(conn.execute("SELECT title, task_type FROM tasks WHERE id = 1").fetchone()) == (
            "Việc cũ", config.TYPE_TEAM
        )
        assert conn.execute("SELECT content FROM task_members WHERE task_id = 1").fetchone()[0] == "Phần cũ"
        assert conn.execute("SELECT action FROM task_logs WHERE task_id = 1").fetchone()[0] == "TASK_CREATED"
        conn.execute(
            "INSERT INTO tasks (title, task_type, status, proposer_id, lead_id) "
            "VALUES ('Việc cá nhân', 'PERSONAL', 'PENDING_APPROVAL', 1, 1)"
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {"idx_tasks_status", "idx_tasks_lead", "idx_tasks_deadline"} <= {
            row["name"] for row in conn.execute("PRAGMA index_list(tasks)")
        }
    finally:
        conn.close()
