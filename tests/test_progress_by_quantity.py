"""Tiến độ theo khối lượng tuyệt đối: chương trình tự tính % = đã hoàn thành / được giao."""
import sqlite3
from datetime import timedelta

import pytest

import config
from core import auth, database, log_service, permissions, task_service as ts
from core.timeutil import today_vn

PASSWORD = "MatKhau@123"


def _future(days=7):
    return (today_vn() + timedelta(days=days)).isoformat()


@pytest.fixture
def users(db_path):
    def make(username, role):
        return auth.get_user(auth.bootstrap_user(username, f"Tên {username}", PASSWORD, role))

    return {
        "manager": make("tpx", config.ROLE_MANAGER),
        "a": make("nv_a", config.ROLE_STAFF),
        "b": make("nv_b", config.ROLE_STAFF),
        "c": make("nv_c", config.ROLE_STAFF),
    }


def _running_task(users, quantities):
    """Việc nhóm đang thực hiện, chủ trì A; B, C nhận phần việc với khối lượng cho trước (None = không có)."""
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Việc", None, _future(), lead_id=users["a"]["id"])
    for key, qty in zip(("b", "c"), quantities):
        ts.add_member(users["a"], task_id, users[key]["id"], f"Phần {key}", qty, "trạm" if qty else None)
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    return task_id


def _member(task_id, user):
    return next(m for m in ts.get_task(task_id)["members"] if m["user_id"] == user["id"])


# --- Công thức -----------------------------------------------------------------
@pytest.mark.parametrize(
    "done,quantity,expected",
    [
        (0, 10, 0),
        (6, 10, 60),
        (1, 3, 33),
        (2, 3, 67),
        (10, 10, 100),
        (12, 10, 100),  # làm vượt vẫn tối đa 100%
        (996, 1000, 99),  # làm tròn không được báo 100% khi chưa đủ
        (0.04, 10, 0),
        (2.5, 10, 25),
        (None, 10, 0),
    ],
)
def test_compute_progress(done, quantity, expected):
    assert ts.compute_progress(done, quantity) == expected


@pytest.mark.parametrize("quantity", [None, 0, -1])
def test_compute_progress_requires_assigned_quantity(quantity):
    with pytest.raises(ValueError):
        ts.compute_progress(5, quantity)


# --- Cập nhật theo khối lượng ------------------------------------------------------
def test_progress_is_computed_from_done_quantity(users):
    task_id = _running_task(users, (20, 3))
    mid = _member(task_id, users["b"])["id"]
    assert _member(task_id, users["b"])["done_quantity"] == 0.0  # phần việc mới: đã làm 0
    ts.update_progress(users["b"], mid, 12, None)
    member = _member(task_id, users["b"])
    assert (member["done_quantity"], member["progress"]) == (12.0, 60)
    assert ts.get_task(task_id)["members"][0]["progress"] == 60
    # Tiến độ TB của công việc (hiển thị ở danh sách) dựa trên % đã tính: B 60%, C 0%
    assert ts.list_tasks()[0]["avg_progress"] == 30


def test_completing_full_quantity_needs_report_and_over_delivery_is_allowed(users):
    task_id = _running_task(users, (10, 5))
    mid = _member(task_id, users["b"])["id"]
    with pytest.raises(ValueError):
        ts.update_progress(users["b"], mid, 10, None)  # đủ khối lượng = 100% nên cần báo cáo
    assert ts.update_progress(users["b"], mid, 13, "Làm vượt 3 trạm") is True
    member = _member(task_id, users["b"])
    assert (member["done_quantity"], member["progress"]) == (13.0, 100)


def test_log_records_absolute_and_percent(users):
    task_id = _running_task(users, (20, 3))
    mid = _member(task_id, users["b"])["id"]
    ts.update_progress(users["b"], mid, 5, "Đã làm 5 trạm")
    log = log_service.get_logs(task_id)[-1]
    assert log["detail"]["done_quantity"] == {"old": 0.0, "new": 5.0}
    assert log["detail"]["progress"] == {"old": 0, "new": 25}
    assert (log["detail"]["quantity"], log["detail"]["unit"]) == (20.0, "trạm")
    from ui import components as c

    text = c.describe_log(log["action"], log["detail"])
    assert "5/20 trạm" in text and "25%" in text


def test_same_done_quantity_does_not_log_again(users):
    task_id = _running_task(users, (20, 3))
    mid = _member(task_id, users["b"])["id"]
    ts.update_progress(users["b"], mid, 5, "x")
    count = len(log_service.get_logs(task_id))
    assert ts.update_progress(users["b"], mid, 5, "x") is False
    assert len(log_service.get_logs(task_id)) == count


def test_mixed_modes_in_one_task(users):
    task_id = _running_task(users, (10, None))  # B theo khối lượng, C theo %
    ts.update_progress(users["b"], _member(task_id, users["b"])["id"], 5, None)
    ts.update_progress(users["c"], _member(task_id, users["c"])["id"], 40, None)
    assert _member(task_id, users["b"])["progress"] == 50
    c_member = _member(task_id, users["c"])
    assert (c_member["progress"], c_member["done_quantity"]) == (40, None)
    with pytest.raises(ValueError):  # C không có khối lượng giao: 150 không phải % hợp lệ
        ts.update_progress(users["c"], c_member["id"], 150, "x")


def test_direct_task_with_quantity_auto_reports_when_all_full(users):
    task_id = ts.create_task(
        users["manager"],
        config.TYPE_DIRECT,
        "Giao trực tiếp",
        None,
        _future(),
        lead_id=users["b"]["id"],
        members=[{"content": "Làm", "quantity": 4, "unit": "bản"}],
    )
    member_id = _member(task_id, users["b"])["id"]
    ts.update_progress(users["b"], member_id, 3, "B gần xong")  # 3/4 = 75%
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS
    ts.update_progress(users["b"], member_id, 4, "B xong")
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED


def test_only_owner_can_update_by_quantity(users):
    task_id = _running_task(users, (10, 5))
    with pytest.raises(permissions.PermissionDenied):
        ts.update_progress(users["c"], _member(task_id, users["b"])["id"], 5, None)


# --- Sửa khối lượng giao thì % tự tính lại -------------------------------------------
def test_changing_assigned_quantity_recomputes_progress(users):
    task_id = _running_task(users, (10, 5))
    member = _member(task_id, users["b"])
    ts.update_progress(users["b"], member["id"], 5, "x")  # 5/10 = 50%
    # Chủ trì tăng khối lượng giao lên 20: 5/20 = 25%
    assert ts.update_member(users["a"], member["id"], "Phần b", 20, "trạm") is True
    after = _member(task_id, users["b"])
    assert (after["quantity"], after["done_quantity"], after["progress"]) == (20.0, 5.0, 25)
    log = log_service.get_logs(task_id)[-1]
    assert log["action"] == config.LOG_MEMBER_UPDATED
    assert log["detail"]["progress"] == {"old": 50, "new": 25}
    # Giảm xuống 4: đã làm 5 >= 4 nên đạt 100%
    ts.update_member(users["a"], member["id"], "Phần b", 4, "trạm")
    assert _member(task_id, users["b"])["progress"] == 100


def test_removing_assigned_quantity_keeps_percent_and_switches_to_percent_mode(users):
    task_id = _running_task(users, (10, 5))
    member = _member(task_id, users["b"])
    ts.update_progress(users["b"], member["id"], 5, "x")  # 50%
    ts.update_member(users["a"], member["id"], "Phần b", None, None)
    after = _member(task_id, users["b"])
    assert (after["quantity"], after["done_quantity"], after["progress"]) == (None, None, 50)
    assert ts.update_progress(users["b"], member["id"], 80, None) is True  # nay nhập theo %
    assert _member(task_id, users["b"])["progress"] == 80


def test_adding_assigned_quantity_converts_existing_percent(users):
    task_id = _running_task(users, (None, 5))
    member = _member(task_id, users["b"])
    ts.update_progress(users["b"], member["id"], 40, None)  # 40%
    ts.update_member(users["a"], member["id"], "Phần b", 50, "trạm")
    after = _member(task_id, users["b"])
    assert (after["done_quantity"], after["progress"]) == (20.0, 40)  # 40% của 50


def test_new_member_with_quantity_starts_at_zero_done(users):
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Nháp", None, _future(), lead_id=users["a"]["id"])
    ts.add_members_bulk(
        users["a"],
        task_id,
        "Kiểm tra",
        "trạm",
        [{"user_id": users["b"]["id"], "quantity": 10}, {"user_id": users["c"]["id"], "quantity": None}],
    )
    b, c = _member(task_id, users["b"]), _member(task_id, users["c"])
    assert (b["done_quantity"], b["progress"]) == (0.0, 0)
    assert (c["done_quantity"], c["progress"]) == (None, 0)


def test_done_quantity_constraint_in_database(users):
    task_id = _running_task(users, (10, 5))
    mid = _member(task_id, users["b"])["id"]
    with database.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE task_members SET done_quantity = -1 WHERE id = ?", (mid,))


def test_list_my_work_exposes_done_quantity(users):
    task_id = _running_task(users, (20, 3))
    ts.update_progress(users["b"], _member(task_id, users["b"])["id"], 8, None)
    part = ts.list_my_work(users["b"]["id"])["parts"][0]
    assert (part["quantity"], part["done_quantity"], part["progress"]) == (20.0, 8.0, 40)
