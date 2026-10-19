"""Kết nối SQLite, schema và transaction.

Schema chỉ gồm 4 bảng nghiệp vụ: users, tasks, task_members, task_logs.
Viết bằng SQL chuẩn để dễ chuyển sang PostgreSQL (khi chuyển: đổi
AUTOINCREMENT -> GENERATED ... AS IDENTITY, TEXT thời gian -> TIMESTAMPTZ,
username NOCASE -> CITEXT hoặc unique index trên lower(username), và thay
trigger bất biến của task_logs bằng quyền INSERT/SELECT).

Phiên bản schema (PRAGMA user_version):
  1 (Giai đoạn 1, chưa đặt user_version): task_logs.member_id có khóa ngoại.
  2 (Giai đoạn 2): task_logs.member_id chỉ là số tham chiếu, không có khóa
    ngoại, để xóa được phần việc mà vẫn giữ nguyên nhật ký bất biến.
  3 (cập nhật tiến độ theo khối lượng): task_members có thêm cột done_quantity
    (khối lượng đã hoàn thành). Với phần việc có khối lượng giao, `progress` (%) là
    giá trị do chương trình tự tính từ done_quantity / quantity.
  4 (giao trực tiếp cho 1 người): bỏ ràng buộc "chỉ công việc nhóm mới có chủ trì"; công
    việc DIRECT có chủ trì chính là người được giao. Dữ liệu cũ có đúng 1 người thực hiện
    được gán chủ trì tự động.
  5 (công việc cá nhân): cho phép task_type PERSONAL, do nhân viên tạo và phải được duyệt.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence, Union

import config

PathLike = Union[str, Path]

SCHEMA_VERSION = 5


def _in_list(values: Sequence[str]) -> str:
    """Danh sách giá trị cố định (từ config) dùng trong CHECK ... IN (...)."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


# Các câu lệnh của task_logs tách riêng để dùng chung cho schema và migration.
_TASK_LOGS_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS task_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks (id),
    member_id  INTEGER,                       -- tham chiếu task_members.id, cố ý không có khóa ngoại
    actor_id   INTEGER NOT NULL REFERENCES users (id),
    action     TEXT NOT NULL,
    detail     TEXT,                          -- JSON
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)""",
    "CREATE INDEX IF NOT EXISTS idx_logs_task_created ON task_logs (task_id, created_at)",
    # Nhật ký bất biến: không cho sửa hoặc xóa.
    """CREATE TRIGGER IF NOT EXISTS trg_task_logs_no_update
BEFORE UPDATE ON task_logs
BEGIN
    SELECT RAISE(ABORT, 'task_logs là nhật ký bất biến: không được sửa.');
END""",
    """CREATE TRIGGER IF NOT EXISTS trg_task_logs_no_delete
BEFORE DELETE ON task_logs
BEGIN
    SELECT RAISE(ABORT, 'task_logs là nhật ký bất biến: không được xóa.');
END""",
]


def _tasks_table_sql(name: str, *, if_not_exists: bool) -> str:
    """Câu lệnh tạo bảng tasks (dùng chung cho schema và migration v4)."""
    clause = "IF NOT EXISTS " if if_not_exists else ""
    return f"""CREATE TABLE {clause}{name} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description   TEXT,
    task_type     TEXT NOT NULL CHECK (task_type IN ({_in_list(config.TASK_TYPES)})),
    status        TEXT NOT NULL DEFAULT 'DRAFT'
                  CHECK (status IN ({_in_list(config.STATUSES)})),
    proposer_id   INTEGER NOT NULL REFERENCES users (id),
    lead_id       INTEGER REFERENCES users (id),
    approver_id   INTEGER REFERENCES users (id),
    deadline      TEXT,                       -- YYYY-MM-DD
    submitted_at  TEXT,
    approved_at   TEXT,
    reject_reason TEXT,
    report_summary TEXT,
    completed_at  TEXT,                       -- thời điểm báo cáo hoàn thành
    eval_result   TEXT CHECK (eval_result IS NULL OR eval_result IN ({_in_list(config.EVAL_RESULTS)})),
    eval_comment  TEXT,
    evaluated_by  INTEGER REFERENCES users (id),
    evaluated_at  TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- Công việc nhóm luôn có chủ trì; công việc giao trực tiếp: chủ trì chính là người được giao
    -- (do nghiệp vụ bảo đảm; dữ liệu cũ trước schema v4 có thể để trống).
    -- Chỉ có kết quả đánh giá khi công việc ở trạng thái Đạt / Không đạt.
    CHECK (eval_result IS NULL OR status IN ('PASSED', 'FAILED'))
)"""


def build_schema_sql() -> str:
    return (
        f"""
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT NOT NULL CHECK (length(trim(full_name)) > 0),
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ({_in_list(config.ROLES)})),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

{_tasks_table_sql('tasks', if_not_exists=True)};

CREATE TABLE IF NOT EXISTS task_members (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        INTEGER NOT NULL REFERENCES tasks (id),
    user_id        INTEGER NOT NULL REFERENCES users (id),
    content        TEXT NOT NULL CHECK (length(trim(content)) > 0),
    quantity       REAL CHECK (quantity IS NULL OR quantity >= 0),
    unit           TEXT,
    done_quantity  REAL CHECK (done_quantity IS NULL OR done_quantity >= 0),  -- đã hoàn thành (cùng đơn vị với quantity)
    progress       INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    result_report  TEXT,
    lead_comment   TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- Mỗi người chỉ có 1 phần việc trong một công việc.
    UNIQUE (task_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status      ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_proposer    ON tasks (proposer_id);
CREATE INDEX IF NOT EXISTS idx_tasks_lead        ON tasks (lead_id);
CREATE INDEX IF NOT EXISTS idx_tasks_deadline    ON tasks (deadline);
CREATE INDEX IF NOT EXISTS idx_members_user      ON task_members (user_id);

"""
        + ";\n\n".join(_TASK_LOGS_STATEMENTS)
        + ";\n"
    )


def get_connection(db_path: Optional[PathLike] = None) -> sqlite3.Connection:
    """Mở kết nối mới. Luôn bật khóa ngoại. Transaction do code tự quản lý."""
    path = Path(db_path) if db_path else config.get_db_path()
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connect(db_path: Optional[PathLike] = None) -> Iterator[sqlite3.Connection]:
    """Kết nối chỉ để đọc (không mở transaction ghi)."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(db_path: Optional[PathLike] = None) -> Iterator[sqlite3.Connection]:
    """Transaction ghi: thành công thì COMMIT, có lỗi thì ROLLBACK toàn bộ.

    Dùng cho mọi thao tác cập nhật liên quan nhiều bảng (ví dụ: đổi trạng
    thái công việc + ghi task_logs).
    """
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _task_logs_has_member_fk(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA foreign_key_list(task_logs)").fetchall()
    return any(r["table"] == "task_members" for r in rows)


def _migrate_task_logs_drop_member_fk(conn: sqlite3.Connection) -> None:
    """Schema v1 -> v2: dựng lại task_logs không có khóa ngoại member_id, giữ nguyên dữ liệu.

    Toàn bộ chạy trong một transaction; trigger bất biến được tạo lại trước khi COMMIT.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TRIGGER IF EXISTS trg_task_logs_no_update")
        conn.execute("DROP TRIGGER IF EXISTS trg_task_logs_no_delete")
        conn.execute("DROP INDEX IF EXISTS idx_logs_task_created")
        conn.execute("ALTER TABLE task_logs RENAME TO task_logs_old")
        conn.execute(_TASK_LOGS_STATEMENTS[0])
        conn.execute(
            "INSERT INTO task_logs (id, task_id, member_id, actor_id, action, detail, created_at) "
            "SELECT id, task_id, member_id, actor_id, action, detail, created_at FROM task_logs_old"
        )
        conn.execute("DROP TABLE task_logs_old")
        for statement in _TASK_LOGS_STATEMENTS[1:]:
            conn.execute(statement)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _migrate_add_done_quantity(conn: sqlite3.Connection) -> None:
    """Schema v2 -> v3: thêm task_members.done_quantity và quy đổi từ tiến độ % cũ.

    Phần việc có khối lượng giao: done_quantity = quantity * progress / 100 (tiến độ % giữ nguyên).
    Phần việc không có khối lượng giao: done_quantity để trống, vẫn nhập tiến độ trực tiếp theo %.
    """
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(task_members)")}
    if "done_quantity" in columns:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "ALTER TABLE task_members ADD COLUMN done_quantity REAL "
            "CHECK (done_quantity IS NULL OR done_quantity >= 0)"
        )
        conn.execute(
            "UPDATE task_members SET done_quantity = quantity * progress / 100.0 "
            "WHERE quantity IS NOT NULL AND quantity > 0"
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _tasks_forbids_direct_lead(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone()
    return bool(row) and "lead_id IS NULL" in (row["sql"] or "")


def _migrate_direct_lead(conn: sqlite3.Connection) -> None:
    """Schema v3 -> v4: dựng lại bảng tasks không còn ràng buộc chủ trì chỉ cho TEAM.

    Theo quy trình dựng lại bảng của SQLite (tắt khóa ngoại, tạo bảng mới, chép, xóa bảng cũ,
    đổi tên). Sau đó công việc DIRECT cũ có đúng 1 người thực hiện được gán người đó làm chủ trì;
    công việc DIRECT cũ có nhiều người giữ nguyên (chủ trì để trống).
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_tasks_table_sql("tasks_new", if_not_exists=False))
            conn.execute("INSERT INTO tasks_new SELECT * FROM tasks")
            conn.execute("DROP TABLE tasks")
            conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
            conn.execute(
                "UPDATE tasks SET lead_id = (SELECT user_id FROM task_members WHERE task_id = tasks.id) "
                "WHERE task_type = 'DIRECT' AND lead_id IS NULL "
                "AND (SELECT COUNT(*) FROM task_members WHERE task_id = tasks.id) = 1"
            )
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("Lỗi khóa ngoại sau khi nâng cấp schema v4.")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _tasks_missing_personal_type(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone()
    return bool(row) and f"'{config.TYPE_PERSONAL}'" not in (row["sql"] or "")


def _migrate_add_personal_type(conn: sqlite3.Connection) -> None:
    """Schema v4 -> v5: rebuild tasks to allow employee-proposed personal tasks."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_tasks_table_sql("tasks_new", if_not_exists=False))
            conn.execute("INSERT INTO tasks_new SELECT * FROM tasks")
            conn.execute("DROP TABLE tasks")
            conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("Lỗi khóa ngoại sau khi nâng cấp schema v5.")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(db_path: Optional[PathLike] = None) -> Path:
    """Tạo database và schema, tự nâng cấp từ phiên bản cũ. An toàn khi chạy lại nhiều lần."""
    path = Path(db_path) if db_path else config.get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    try:
        if _table_exists(conn, "task_logs") and _task_logs_has_member_fk(conn):
            _migrate_task_logs_drop_member_fk(conn)
        if _table_exists(conn, "tasks") and _tasks_forbids_direct_lead(conn):
            _migrate_direct_lead(conn)
        if _table_exists(conn, "tasks") and _tasks_missing_personal_type(conn):
            _migrate_add_personal_type(conn)
        conn.executescript(build_schema_sql())
        _migrate_add_done_quantity(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()
    return path
