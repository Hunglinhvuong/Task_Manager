import sqlite3
from datetime import timedelta

import pytest

import config
from core import auth, database, log_service, permissions, task_service as ts
from core.timeutil import today_vn

PASSWORD = "MatKhau@123"


def _future(days=7):
    return (today_vn() + timedelta(days=days)).isoformat()


def _past(days=1):
    return (today_vn() - timedelta(days=days)).isoformat()


@pytest.fixture
def users(db_path):
    def make(username, role):
        uid = auth.bootstrap_user(username, f"Tên {username}", PASSWORD, role)
        return auth.get_user(uid)

    return {
        "admin": make("admin", config.ROLE_ADMIN),
        "manager": make("tpx", config.ROLE_MANAGER),
        "a": make("nv_a", config.ROLE_STAFF),
        "b": make("nv_b", config.ROLE_STAFF),
        "c": make("nv_c", config.ROLE_STAFF),
    }


def _direct(users, assignee="a", title="Việc giao"):
    """Giao trực tiếp cho đúng một người; người đó là chủ trì."""
    return ts.create_task(
        users["manager"],
        config.TYPE_DIRECT,
        title,
        "Mô tả",
        _future(),
        lead_id=users[assignee]["id"],
        members=[{"content": "Làm phần của mình", "quantity": 3, "unit": "báo cáo"}],
    )


def _personal(users, proposer="a", title="Việc cá nhân"):
    person = users[proposer]
    return ts.create_task(
        person,
        config.TYPE_PERSONAL,
        title,
        "Mô tả",
        _future(),
        lead_id=person["id"],
        members=[{"content": "Tự thực hiện", "quantity": 3, "unit": "báo cáo"}],
    )


def _team(users, proposer="a", lead="a", title="Việc nhóm"):
    return ts.create_task(
        users[proposer], config.TYPE_TEAM, title, None, _future(), lead_id=users[lead]["id"]
    )


def _team_ready(users, proposer="a", lead="a"):
    """Việc nhóm đã có 1 phần việc, sẵn sàng gửi phê duyệt."""
    task_id = _team(users, proposer, lead)
    ts.add_member(users[lead], task_id, users["b"]["id"], "Phần của B", 2, "giờ")
    return task_id


def _actions(task_id):
    return [log["action"] for log in log_service.get_logs(task_id)]


# --- Tạo công việc -----------------------------------------------------
def test_create_permissions(users):
    member = [{"content": "x"}]
    a = users["a"]["id"]
    with pytest.raises(permissions.PermissionDenied):
        ts.create_task(users["a"], config.TYPE_DIRECT, "T", None, _future(), lead_id=a, members=member)
    with pytest.raises(permissions.PermissionDenied):
        ts.create_task(users["admin"], config.TYPE_TEAM, "T", None, _future(), lead_id=a)
    with pytest.raises(permissions.PermissionDenied):
        ts.create_task(users["admin"], config.TYPE_DIRECT, "T", None, _future(), lead_id=a, members=member)
    assert _team(users, "a", "a")
    assert _team(users, "manager", "b")
    assert _direct(users)
    assert _personal(users)
    with pytest.raises(permissions.PermissionDenied):
        ts.create_task(
            users["manager"],
            config.TYPE_PERSONAL,
            "Không được tạo",
            None,
            _future(),
            lead_id=users["manager"]["id"],
            members=[{"content": "Việc"}],
        )
    assert not permissions.can_create_task(users["admin"], config.TYPE_PERSONAL)


def test_personal_task_requires_approval_then_auto_reports_like_direct(users):
    task_id = _personal(users)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_PENDING_APPROVAL
    assert task["submitted_at"]
    assert task["approved_at"] is None
    assert task["approver_id"] is None
    assert task["proposer_id"] == task["lead_id"] == users["a"]["id"]
    assert len(task["members"]) == 1
    assert task["members"][0]["user_id"] == users["a"]["id"]
    assert not permissions.can_edit_allocation(users["a"], task)
    with pytest.raises(ValueError, match="Chờ phê duyệt"):
        ts.update_member(users["a"], task["members"][0]["id"], "Thay khi chờ duyệt")
    assert _actions(task_id) == [
        config.LOG_TASK_CREATED,
        config.LOG_MEMBER_ADDED,
        config.LOG_SUBMITTED,
    ]

    with pytest.raises(permissions.PermissionDenied):
        ts.approve_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_IN_PROGRESS
    assert task["approver_id"] == users["manager"]["id"]

    ts.update_progress(users["a"], task["members"][0]["id"], 3, "Đã hoàn thành")
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED


def test_personal_task_must_be_assigned_to_creator(users):
    with pytest.raises(ValueError, match="chính người tạo"):
        ts.create_task(
            users["a"],
            config.TYPE_PERSONAL,
            "Giao cho người khác",
            None,
            _future(),
            lead_id=users["b"]["id"],
            members=[{"user_id": users["b"]["id"], "content": "Phần việc"}],
        )
    with pytest.raises(ValueError, match="đúng một phần việc"):
        ts.create_task(
            users["a"],
            config.TYPE_PERSONAL,
            "Thiếu phần việc",
            None,
            _future(),
            lead_id=users["a"]["id"],
        )


def test_rejected_personal_task_can_be_edited_and_resubmitted(users):
    task_id = _personal(users)
    ts.reject_task(users["manager"], task_id, "Cần bổ sung nội dung")
    task = ts.get_task(task_id)
    member_id = task["members"][0]["id"]
    ts.update_task(
        users["a"], task_id, title="Đã bổ sung", description=None, deadline=_future()
    )
    ts.update_member(users["a"], member_id, "Nội dung đã bổ sung", 4, "báo cáo")
    ts.submit_task(users["a"], task_id)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_PENDING_APPROVAL
    assert task["title"] == "Đã bổ sung"
    assert task["members"][0]["quantity"] == 4
    assert _actions(task_id)[-1] == config.LOG_SUBMITTED
    assert log_service.get_logs(task_id)[-1]["detail"]["resubmitted"] is True

    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["b"]["id"], "Thêm người")


def test_direct_task_goes_straight_to_in_progress_with_logs(users):
    task_id = _direct(users)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_IN_PROGRESS
    assert task["approver_id"] == users["manager"]["id"]
    assert task["approved_at"]
    assert task["lead_id"] == users["a"]["id"]  # người được giao là chủ trì
    assert task["lead_name"] == "Tên nv_a"
    assert [m["full_name"] for m in task["members"]] == ["Tên nv_a"]
    assert _actions(task_id) == [
        config.LOG_TASK_CREATED,
        config.LOG_MEMBER_ADDED,
        config.LOG_APPROVED,
    ]
    assert log_service.get_logs(task_id)[0]["detail"]["lead"] == "Tên nv_a"
    assert log_service.get_logs(task_id)[-1]["detail"] == {"auto": True}


def test_direct_task_validation(users):
    manager = users["manager"]
    a, b = users["a"]["id"], users["b"]["id"]
    part = {"content": "x"}

    def create(**kwargs):
        return ts.create_task(manager, config.TYPE_DIRECT, "T", None, _future(), **kwargs)

    with pytest.raises(ValueError, match="người được giao"):  # thiếu người được giao
        create(members=[part])
    with pytest.raises(ValueError, match="một người"):  # không có phần việc
        create(lead_id=a, members=[])
    with pytest.raises(ValueError, match="một người"):  # hai người
        create(lead_id=a, members=[{**part, "user_id": a}, {**part, "user_id": b}])
    with pytest.raises(ValueError, match="một người"):  # phần việc của người khác chủ trì
        create(lead_id=a, members=[{**part, "user_id": b}])
    with pytest.raises(ValueError):  # nội dung trống
        create(lead_id=a, members=[{"content": "  "}])
    with pytest.raises(ValueError):  # người không tồn tại
        create(lead_id=9999, members=[part])
    with pytest.raises(ValueError):  # quản trị viên không được giao việc
        create(lead_id=users["admin"]["id"], members=[part])
    # user_id trong phần việc là tùy chọn, nếu có thì phải trùng người được giao
    assert create(lead_id=a, members=[{**part, "user_id": a}])
    assert create(lead_id=a, members=[part])
    # Trưởng phòng có thể tự giao cho mình
    assert create(lead_id=manager["id"], members=[part])


@pytest.mark.parametrize(
    "title,deadline",
    [("", None), ("   ", None), ("T", _past()), ("T", "khong-phai-ngay"), ("x" * 201, None)],
)
def test_invalid_title_or_deadline_rejected(users, title, deadline):
    with pytest.raises(ValueError):
        ts.create_task(
            users["a"],
            config.TYPE_TEAM,
            title,
            None,
            deadline if deadline is not None else _future(),
            lead_id=users["a"]["id"],
        )


def test_team_requires_valid_lead(users):
    with pytest.raises(ValueError):
        ts.create_task(users["a"], config.TYPE_TEAM, "T", None, _future(), lead_id=None)
    with pytest.raises(ValueError):  # ADMIN không được làm chủ trì
        ts.create_task(users["a"], config.TYPE_TEAM, "T", None, _future(), lead_id=users["admin"]["id"])
    with pytest.raises(ValueError):
        ts.create_task(users["a"], config.TYPE_TEAM, "T", None, _future(), lead_id=9999)
    auth.set_active(users["admin"], users["c"]["id"], False)
    with pytest.raises(ValueError):  # tài khoản đã khóa
        ts.create_task(users["a"], config.TYPE_TEAM, "T", None, _future(), lead_id=users["c"]["id"])


def test_team_task_starts_as_draft(users):
    task_id = _team(users, "a", "b")
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_DRAFT
    assert task["proposer_id"] == users["a"]["id"]
    assert task["lead_id"] == users["b"]["id"]
    assert task["approver_id"] is None
    assert _actions(task_id) == [config.LOG_TASK_CREATED]


def test_failed_create_rolls_back_everything(users):
    with pytest.raises(ValueError):
        ts.create_task(
            users["manager"],
            config.TYPE_DIRECT,
            "Sẽ bị hủy",
            None,
            _future(),
            members=[
                {"user_id": users["a"]["id"], "content": "ok"},
                {"user_id": 9999, "content": "người không tồn tại"},
            ],
        )
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_members").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0] == 0


# --- Sửa công việc -------------------------------------------------------
def test_update_task_permissions_and_status(users):
    task_id = _team(users, "a", "a")
    # Người không liên quan không sửa được
    with pytest.raises(permissions.PermissionDenied):
        ts.update_task(users["c"], task_id, title="Hack", description=None, deadline=_future(), lead_id=users["a"]["id"])
    # Người chủ trì (không phải người đề xuất) cũng không sửa thông tin được
    task_b = _team(users, "a", "b")
    with pytest.raises(permissions.PermissionDenied):
        ts.update_task(users["b"], task_b, title="Hack", description=None, deadline=_future(), lead_id=users["b"]["id"])
    # Người đề xuất sửa được bản nháp, và ghi nhật ký thay đổi
    assert ts.update_task(users["a"], task_id, title="Tên mới", description="Mô tả mới", deadline=_future(10), lead_id=users["b"]["id"])
    task = ts.get_task(task_id)
    assert task["title"] == "Tên mới"
    assert task["description"] == "Mô tả mới"
    assert task["lead_id"] == users["b"]["id"]
    detail = log_service.get_logs(task_id)[-1]["detail"]
    assert detail["title"] == {"old": "Việc nhóm", "new": "Tên mới"}
    assert detail["lead"] == {"old": "Tên nv_a", "new": "Tên nv_b"}


def test_update_without_changes_is_noop(users):
    task_id = _team(users)
    before = _actions(task_id)
    assert ts.update_task(users["a"], task_id, title="Việc nhóm", description=None, deadline=_future(), lead_id=users["a"]["id"]) is False
    assert _actions(task_id) == before


def test_update_deadline_to_past_rejected_but_unchanged_old_deadline_ok(users):
    task_id = _direct(users)
    with pytest.raises(ValueError):
        ts.update_task(users["manager"], task_id, title="Việc giao", description="Mô tả", deadline=_past())
    # Đổi tên mà giữ nguyên hạn thì không bị kiểm tra "quá khứ"
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET deadline = ? WHERE id = ?", (_past(3), task_id))
    assert ts.update_task(users["manager"], task_id, title="Đổi tên", description="Mô tả", deadline=_past(3))


def test_staff_cannot_edit_in_progress_but_manager_can(users):
    task_id = _direct(users)
    with pytest.raises(permissions.PermissionDenied):
        ts.update_task(users["a"], task_id, title="Hack", description=None, deadline=_future())
    assert ts.update_task(users["manager"], task_id, title="Đã sửa", description=None, deadline=_future())


def test_cannot_edit_while_pending_or_when_finished(users):
    task_id = _team_ready(users)
    ts.submit_task(users["a"], task_id)
    for actor in (users["a"], users["manager"]):
        with pytest.raises(ValueError):
            ts.update_task(actor, task_id, title="X", description=None, deadline=_future(), lead_id=users["a"]["id"])


def test_direct_task_assignee_cannot_be_changed(users):
    task_id = _direct(users)
    kwargs = dict(title="Việc giao", description="Mô tả", deadline=_future())
    with pytest.raises(ValueError, match="Không đổi người được giao"):
        ts.update_task(users["manager"], task_id, lead_id=users["b"]["id"], **kwargs)
    # Bỏ trống hoặc giữ nguyên người được giao thì hợp lệ (không thay đổi gì)
    assert ts.update_task(users["manager"], task_id, lead_id=None, **kwargs) is False
    assert ts.update_task(users["manager"], task_id, lead_id=users["a"]["id"], **kwargs) is False
    assert ts.get_task(task_id)["lead_id"] == users["a"]["id"]


# --- Gửi phê duyệt / duyệt / từ chối -------------------------------------
def test_submit_requires_members_and_proper_actor(users):
    task_id = _team(users, "a", "b")
    with pytest.raises(ValueError):  # chưa có phần việc nào
        ts.submit_task(users["a"], task_id)
    ts.add_member(users["a"], task_id, users["c"]["id"], "Phần của C")
    with pytest.raises(permissions.PermissionDenied):  # người chủ trì (không phải đề xuất) không được gửi
        ts.submit_task(users["b"], task_id)
    with pytest.raises(permissions.PermissionDenied):
        ts.submit_task(users["c"], task_id)
    with pytest.raises(permissions.PermissionDenied):
        ts.submit_task(users["admin"], task_id)
    ts.submit_task(users["a"], task_id)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_PENDING_APPROVAL
    assert task["submitted_at"]
    with pytest.raises(ValueError):  # đã gửi rồi
        ts.submit_task(users["a"], task_id)


def test_manager_can_submit_someone_elses_draft(users):
    task_id = _team_ready(users)
    ts.submit_task(users["manager"], task_id)
    assert ts.get_task(task_id)["status"] == config.STATUS_PENDING_APPROVAL


def test_submit_blocked_when_deadline_passed_or_member_locked(users):
    task_id = _team_ready(users)
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET deadline = ? WHERE id = ?", (_past(), task_id))
    with pytest.raises(ValueError):
        ts.submit_task(users["a"], task_id)
    ts.update_task(users["a"], task_id, title="Việc nhóm", description=None, deadline=_future(), lead_id=users["a"]["id"])
    auth.set_active(users["admin"], users["b"]["id"], False)
    with pytest.raises(ValueError):
        ts.submit_task(users["a"], task_id)


def test_direct_task_cannot_be_submitted(users):
    task_id = _direct(users)
    with pytest.raises(ValueError):
        ts.submit_task(users["manager"], task_id)


def test_approve_flow_and_permissions(users):
    task_id = _team_ready(users)
    ts.submit_task(users["a"], task_id)
    for actor in (users["a"], users["b"], users["admin"]):
        with pytest.raises(permissions.PermissionDenied):
            ts.approve_task(actor, task_id)
    ts.approve_task(users["manager"], task_id)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_IN_PROGRESS
    assert task["approver_id"] == users["manager"]["id"]
    assert task["approved_at"]
    assert _actions(task_id)[-2:] == [config.LOG_SUBMITTED, config.LOG_APPROVED]
    with pytest.raises(ValueError):  # không duyệt lại lần hai
        ts.approve_task(users["manager"], task_id)


def test_manager_can_approve_own_proposal(users):
    task_id = _team(users, "manager", "a")
    ts.add_member(users["manager"], task_id, users["b"]["id"], "Phần B")
    ts.submit_task(users["manager"], task_id)
    ts.approve_task(users["manager"], task_id)
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS


def test_cannot_approve_draft(users):
    task_id = _team_ready(users)
    with pytest.raises(ValueError):
        ts.approve_task(users["manager"], task_id)


def test_reject_requires_reason_then_edit_and_resubmit(users):
    task_id = _team_ready(users)
    ts.submit_task(users["a"], task_id)
    with pytest.raises(ValueError):
        ts.reject_task(users["manager"], task_id, "   ")
    with pytest.raises(permissions.PermissionDenied):
        ts.reject_task(users["a"], task_id, "Tự từ chối")

    ts.reject_task(users["manager"], task_id, "Thiếu khối lượng")
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_REJECTED
    assert task["reject_reason"] == "Thiếu khối lượng"
    assert log_service.get_logs(task_id)[-1]["detail"] == {"reason": "Thiếu khối lượng"}

    # Người đề xuất sửa lại và gửi duyệt lại
    ts.update_task(users["a"], task_id, title="Đã chỉnh", description=None, deadline=_future(), lead_id=users["a"]["id"])
    member_id = task["members"][0]["id"]
    ts.update_member(users["a"], member_id, "Phần B đã rõ hơn", 5, "báo cáo")
    ts.submit_task(users["a"], task_id)
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_PENDING_APPROVAL
    assert task["reject_reason"] is None
    assert log_service.get_logs(task_id)[-1]["detail"]["resubmitted"] is True

    ts.approve_task(users["manager"], task_id)
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS


# --- Phân công phần việc -------------------------------------------------
def test_allocation_permissions_by_status(users):
    task_id = _team(users, proposer="a", lead="b")
    # Bản nháp: người đề xuất, chủ trì và trưởng phòng được phân công; người khác thì không
    ts.add_member(users["a"], task_id, users["c"]["id"], "Phần C")
    ts.add_member(users["b"], task_id, users["a"]["id"], "Phần A")
    with pytest.raises(permissions.PermissionDenied):
        ts.add_member(users["c"], task_id, users["manager"]["id"], "Không được")
    with pytest.raises(permissions.PermissionDenied):
        ts.add_member(users["admin"], task_id, users["manager"]["id"], "Không được")

    # Chờ phê duyệt: không ai được đổi phân công
    ts.submit_task(users["a"], task_id)
    for actor in (users["a"], users["b"], users["manager"]):
        with pytest.raises(ValueError):
            ts.add_member(actor, task_id, users["manager"]["id"], "Muộn")

    # Đang thực hiện: chỉ chủ trì và trưởng phòng; người đề xuất (không chủ trì) thì không
    ts.approve_task(users["manager"], task_id)
    with pytest.raises(permissions.PermissionDenied):
        ts.add_member(users["a"], task_id, users["manager"]["id"], "Không được")
    ts.add_member(users["b"], task_id, users["manager"]["id"], "Trưởng phòng hỗ trợ")
    assert len(ts.get_task(task_id)["members"]) == 3


def test_direct_allocation_only_manager(users):
    task_id = _direct(users)
    member_id = ts.get_task(task_id)["members"][0]["id"]
    # Người được giao (chủ trì) không tự đổi phân công của mình; trưởng phòng được sửa phần việc
    with pytest.raises(permissions.PermissionDenied):
        ts.update_member(users["a"], member_id, "Tự sửa", 1, "x")
    assert ts.update_member(users["manager"], member_id, "Nội dung mới", 5, "báo cáo")
    # Không thêm/xóa người: chỉ có đúng một người thực hiện
    with pytest.raises(ValueError, match="một người"):
        ts.add_member(users["manager"], task_id, users["c"]["id"], "Thêm người")
    with pytest.raises(ValueError, match="một người"):
        ts.add_members_bulk(users["manager"], task_id, "Việc", None, [{"user_id": users["c"]["id"]}])
    with pytest.raises(ValueError, match="một người"):
        ts.remove_member(users["manager"], member_id)
    assert len(ts.get_task(task_id)["members"]) == 1


def test_direct_assignee_cannot_comment_on_own_part_but_manager_can(users):
    task_id = _direct(users)
    member_id = ts.get_task(task_id)["members"][0]["id"]
    with pytest.raises(permissions.PermissionDenied):
        ts.comment_member(users["a"], member_id, "Tự nhận xét")
    assert ts.comment_member(users["manager"], member_id, "Làm tốt")


def test_one_part_per_person(users):
    task_id = _team(users)
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần 1")
    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["b"]["id"], "Phần 2")


def test_lead_can_take_a_part_too(users):
    task_id = _team(users, "a", "a")
    ts.add_member(users["a"], task_id, users["a"]["id"], "Chủ trì tự làm một phần")
    assert len(ts.get_task(task_id)["members"]) == 1


def test_add_member_validation(users):
    task_id = _team(users)
    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["admin"]["id"], "ADMIN không được giao việc")
    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["b"]["id"], "   ")
    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["b"]["id"], "Việc", quantity=-1)
    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["b"]["id"], "Việc", quantity="abc")
    with pytest.raises(ValueError):
        ts.add_member(users["a"], task_id, users["b"]["id"], "Việc", quantity=float("inf"))
    with pytest.raises(ValueError):
        ts.add_member(users["a"], 9999, users["b"]["id"], "Việc")
    ts.add_member(users["a"], task_id, users["b"]["id"], "Việc", quantity=None, unit=None)


def test_update_member_logs_changes_and_noop(users):
    task_id = _team(users)
    member_id = ts.add_member(users["a"], task_id, users["b"]["id"], "Phần 1", 1, "giờ")
    assert ts.update_member(users["a"], member_id, "Phần 1 sửa", 2.5, "ngày") is True
    member = ts.get_task(task_id)["members"][0]
    assert (member["content"], member["quantity"], member["unit"]) == ("Phần 1 sửa", 2.5, "ngày")
    log = log_service.get_logs(task_id)[-1]
    assert log["action"] == config.LOG_MEMBER_UPDATED
    assert log["member_id"] == member_id
    assert log["detail"]["quantity"] == {"old": 1.0, "new": 2.5}

    count = len(log_service.get_logs(task_id))
    assert ts.update_member(users["a"], member_id, "Phần 1 sửa", 2.5, "ngày") is False
    assert len(log_service.get_logs(task_id)) == count

    with pytest.raises(permissions.PermissionDenied):
        ts.update_member(users["c"], member_id, "Hack", 1, "x")


def test_remove_member_keeps_immutable_log(users):
    task_id = _team(users)
    member_id = ts.add_member(users["a"], task_id, users["b"]["id"], "Phần 1", 1, "giờ")
    ts.remove_member(users["a"], member_id)
    assert ts.get_task(task_id)["members"] == []
    logs = log_service.get_logs(task_id)
    assert [log["action"] for log in logs][-2:] == [config.LOG_MEMBER_ADDED, config.LOG_MEMBER_REMOVED]
    assert logs[-1]["detail"]["user_name"] == "Tên nv_b"
    assert logs[-1]["member_id"] == member_id  # nhật ký vẫn giữ tham chiếu dù phần việc đã xóa
    with pytest.raises(ValueError):
        ts.remove_member(users["a"], member_id)


def test_cannot_remove_member_with_progress_or_report(users):
    task_id = _team(users, "a", "a")
    ts.add_member(users["a"], task_id, users["b"]["id"], "B", 2, "giờ")
    ts.add_member(users["a"], task_id, users["c"]["id"], "C", 2, "giờ")
    members = ts.get_task(task_id)["members"]
    with database.transaction() as conn:
        conn.execute("UPDATE task_members SET progress = 30 WHERE id = ?", (members[0]["id"],))
        conn.execute("UPDATE task_members SET result_report = 'Đã xong' WHERE id = ?", (members[1]["id"],))
    for member in members:
        with pytest.raises(ValueError):
            ts.remove_member(users["manager"], member["id"])
    assert len(ts.get_task(task_id)["members"]) == 2


def test_member_ops_never_touch_logs_of_other_tasks(users):
    t1 = _team(users, title="Một")
    t2 = _team(users, title="Hai")
    ts.add_member(users["a"], t1, users["b"]["id"], "x")
    assert _actions(t2) == [config.LOG_TASK_CREATED]


# --- Truy vấn ------------------------------------------------------------
def test_get_task_not_found(db_path):
    assert ts.get_task(12345) is None


def test_list_assignable_users_excludes_admin_and_inactive(users):
    auth.set_active(users["admin"], users["c"]["id"], False)
    names = [u["username"] for u in ts.list_assignable_users()]
    assert "admin" not in names
    assert "nv_c" not in names
    assert {"tpx", "nv_a", "nv_b"} <= set(names)


def test_list_tasks_filters(users):
    t_direct = _direct(users, title="Báo cáo tuần")
    t_team = _team(users, "b", "b", title="Kiểm tra % hệ thống_x")
    t_draft = _team(users, "c", "c", title="Việc của C")

    assert {t["id"] for t in ts.list_tasks()} == {t_direct, t_team, t_draft}
    assert {t["id"] for t in ts.list_tasks(task_type=config.TYPE_DIRECT)} == {t_direct}
    assert {t["id"] for t in ts.list_tasks(statuses=[config.STATUS_DRAFT])} == {t_team, t_draft}
    assert {t["id"] for t in ts.list_tasks(search="BÁO CÁO")} == {t_direct}  # không phân biệt hoa thường
    assert {t["id"] for t in ts.list_tasks(search="%")} == {t_team}  # ký tự đặc biệt tìm đúng nghĩa đen
    assert {t["id"] for t in ts.list_tasks(search="_x")} == {t_team}
    assert {t["id"] for t in ts.list_tasks(involved_user_id=users["a"]["id"])} == {t_direct}
    assert {t["id"] for t in ts.list_tasks(involved_user_id=users["b"]["id"])} == {t_team}
    assert ts.list_tasks(search="không có gì khớp") == []


def test_list_tasks_shows_names_member_count_and_progress(users):
    task_id = _team(users, "a", "a")
    ts.add_member(users["a"], task_id, users["b"]["id"], "B", 2, "giờ")
    ts.add_member(users["a"], task_id, users["c"]["id"], "C", 2, "giờ")
    members = ts.get_task(task_id)["members"]
    with database.transaction() as conn:
        conn.execute("UPDATE task_members SET progress = 100 WHERE id = ?", (members[0]["id"],))
    row = ts.list_tasks()[0]
    assert row["lead_name"] == "Tên nv_a"
    assert row["member_count"] == 2
    assert row["avg_progress"] == 50
    assert row["proposer_name"] == "Tên nv_a"


def test_overdue_detection(users):
    t_late = _direct(users, title="Trễ hạn")
    t_ok = _direct(users, title="Còn hạn")
    t_reported = _direct(users, title="Đã báo cáo")
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET deadline = ? WHERE id = ?", (_past(), t_late))
        conn.execute(
            "UPDATE tasks SET deadline = ?, status = 'REPORTED' WHERE id = ?", (_past(), t_reported)
        )
    overdue = ts.list_tasks(overdue_only=True)
    assert [t["id"] for t in overdue] == [t_late]
    assert ts.get_task(t_late)["overdue"] is True
    assert ts.get_task(t_ok)["overdue"] is False
    assert ts.get_task(t_reported)["overdue"] is False


def test_pending_approvals_list(users):
    t1 = _team_ready(users)
    t2 = _team_ready(users, proposer="b", lead="b")
    assert ts.list_pending_approvals() == []
    ts.submit_task(users["a"], t1)
    ts.submit_task(users["b"], t2)
    assert [t["id"] for t in ts.list_pending_approvals()] == [t1, t2]
    ts.approve_task(users["manager"], t1)
    assert [t["id"] for t in ts.list_pending_approvals()] == [t2]


def test_whole_workflow_is_fully_logged(users):
    task_id = _team(users, "a", "a")
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B")
    ts.submit_task(users["a"], task_id)
    ts.reject_task(users["manager"], task_id, "Làm lại")
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    assert _actions(task_id) == [
        config.LOG_TASK_CREATED,
        config.LOG_MEMBER_ADDED,
        config.LOG_SUBMITTED,
        config.LOG_REJECTED,
        config.LOG_SUBMITTED,
        config.LOG_APPROVED,
    ]
    assert all(log["actor_name"] for log in log_service.get_logs(task_id))


def test_logs_cannot_be_modified_after_workflow(users):
    task_id = _direct(users)
    with database.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM task_logs WHERE task_id = ?", (task_id,))


# --- Phân công hàng loạt (nhập bảng) ----------------------------------------
def test_bulk_add_same_work_different_quantities(users):
    task_id = _team(users, "a", "a")
    ids = ts.add_members_bulk(
        users["a"],
        task_id,
        "Kiểm tra trạm",
        "trạm",
        [
            {"user_id": users["a"]["id"], "quantity": 10},
            {"user_id": users["b"]["id"], "quantity": 25},
            {"user_id": users["c"]["id"], "quantity": None},
        ],
    )
    assert len(ids) == 3
    members = ts.get_task(task_id)["members"]
    assert [(m["full_name"], m["quantity"], m["unit"], m["content"]) for m in members] == [
        ("Tên nv_a", 10.0, "trạm", "Kiểm tra trạm"),
        ("Tên nv_b", 25.0, "trạm", "Kiểm tra trạm"),
        ("Tên nv_c", None, "trạm", "Kiểm tra trạm"),
    ]
    # Mỗi người một dòng nhật ký riêng, gắn đúng phần việc
    added = [l for l in log_service.get_logs(task_id) if l["action"] == config.LOG_MEMBER_ADDED]
    assert [l["member_id"] for l in added] == ids
    assert [l["detail"]["quantity"] for l in added] == [10.0, 25.0, None]


def test_bulk_add_is_all_or_nothing(users):
    task_id = _team(users, "a", "a")
    ts.add_member(users["a"], task_id, users["b"]["id"], "Có sẵn")
    before = _actions(task_id)
    with pytest.raises(ValueError):  # B đã có phần việc -> cả lô bị hủy, kể cả C hợp lệ
        ts.add_members_bulk(
            users["a"],
            task_id,
            "Chung",
            "giờ",
            [{"user_id": users["c"]["id"], "quantity": 1}, {"user_id": users["b"]["id"], "quantity": 2}],
        )
    assert [m["full_name"] for m in ts.get_task(task_id)["members"]] == ["Tên nv_b"]
    assert _actions(task_id) == before


def test_bulk_add_validation(users):
    task_id = _team(users, "a", "a")
    a, b = users["a"], users["b"]
    with pytest.raises(ValueError):
        ts.add_members_bulk(a, task_id, "Việc", "x", [])
    with pytest.raises(ValueError):
        ts.add_members_bulk(a, task_id, "  ", "x", [{"user_id": b["id"], "quantity": 1}])
    with pytest.raises(ValueError):
        ts.add_members_bulk(a, task_id, "Việc", "x", [{"user_id": b["id"], "quantity": -5}])
    with pytest.raises(ValueError):
        ts.add_members_bulk(a, task_id, "Việc", "x", [{"user_id": b["id"], "quantity": 1}] * 2)
    with pytest.raises(ValueError):
        ts.add_members_bulk(a, task_id, "Việc", "x", [{"user_id": users["admin"]["id"], "quantity": 1}])
    with pytest.raises(ValueError):
        ts.add_members_bulk(a, 9999, "Việc", "x", [{"user_id": b["id"], "quantity": 1}])
    assert ts.get_task(task_id)["members"] == []


def test_bulk_add_respects_allocation_permissions(users):
    task_id = _team(users, "a", "a")
    row = [{"user_id": users["b"]["id"], "quantity": 1}]
    with pytest.raises(permissions.PermissionDenied):
        ts.add_members_bulk(users["c"], task_id, "Việc", "x", row)
    with pytest.raises(permissions.PermissionDenied):
        ts.add_members_bulk(users["admin"], task_id, "Việc", "x", row)
    ts.add_members_bulk(users["manager"], task_id, "Việc", "x", row)
    # Khi chờ phê duyệt thì khóa
    ts.submit_task(users["a"], task_id)
    with pytest.raises(ValueError):
        ts.add_members_bulk(users["a"], task_id, "Việc", "x", [{"user_id": users["c"]["id"], "quantity": 1}])
