"""Ma trận phân quyền: mỗi vai trò × mỗi trạng thái × mỗi thao tác.

Quy tắc kiểm tra cho từng tổ hợp:
- hàm kiểm quyền trả True  => dịch vụ KHÔNG được ném PermissionDenied (có thể ném ValueError do nghiệp vụ);
- hàm kiểm quyền trả False => dịch vụ phải từ chối (PermissionDenied/ValueError) và dữ liệu KHÔNG đổi.
Đây là bằng chứng rằng lớp dịch vụ tự kiểm quyền, không chỉ dựa vào việc giao diện ẩn nút.
"""
from datetime import timedelta

import pytest

import config
from core import auth, database, permissions as perm, task_service as ts
from core.timeutil import today_vn

PASSWORD = "MatKhau@123"
STATUSES_ORDER = [
    config.STATUS_DRAFT,
    config.STATUS_PENDING_APPROVAL,
    config.STATUS_REJECTED,
    config.STATUS_IN_PROGRESS,
    config.STATUS_REPORTED,
    config.STATUS_PASSED,
    config.STATUS_FAILED,
]
ACTORS = ["proposer", "lead", "member", "outsider", "manager", "admin"]


def _future(days=10):
    return (today_vn() + timedelta(days=days)).isoformat()


@pytest.fixture
def world(db_path):
    def make(username, role):
        return auth.get_user(auth.bootstrap_user(username, f"Tên {username}", PASSWORD, role))

    return {
        "manager": make("tpx", config.ROLE_MANAGER),
        "proposer": make("nv_p", config.ROLE_STAFF),
        "lead": make("nv_l", config.ROLE_STAFF),
        "member": make("nv_m", config.ROLE_STAFF),
        "outsider": make("nv_o", config.ROLE_STAFF),
        "extra": make("nv_x", config.ROLE_STAFF),
        "admin": make("quantri", config.ROLE_ADMIN),
    }


def _build(w, status):
    """Tạo công việc nhóm mới và đưa tới đúng trạng thái yêu cầu."""
    p, m = w["proposer"], w["manager"]
    tid = ts.create_task(p, config.TYPE_TEAM, "Việc kiểm thử", None, _future(), lead_id=w["lead"]["id"])
    ts.add_member(p, tid, w["member"]["id"], "Phần của thành viên", 10, "bản")
    if status == config.STATUS_DRAFT:
        return tid
    ts.submit_task(p, tid)
    if status == config.STATUS_PENDING_APPROVAL:
        return tid
    if status == config.STATUS_REJECTED:
        ts.reject_task(m, tid, "Chưa đạt yêu cầu")
        return tid
    ts.approve_task(m, tid)
    if status == config.STATUS_IN_PROGRESS:
        return tid
    for mem in ts.get_task(tid)["members"]:
        user = w["member"] if mem["user_id"] == w["member"]["id"] else w["lead"]
        ts.update_progress(user, mem["id"], mem["quantity"] if ts.has_quantity(mem["quantity"]) else 100, "Xong")
    ts.report_completion(w["lead"], tid, "Đã hoàn thành")
    if status == config.STATUS_REPORTED:
        return tid
    result = config.EVAL_PASS if status == config.STATUS_PASSED else config.EVAL_FAIL
    ts.evaluate_task(m, tid, result, "Nhận xét")
    return tid


def _snapshot(tid):
    with database.transaction() as conn:
        task = tuple(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
        members = [tuple(r) for r in conn.execute("SELECT * FROM task_members WHERE task_id = ? ORDER BY id", (tid,))]
        logs = conn.execute("SELECT COUNT(*) FROM task_logs WHERE task_id = ?", (tid,)).fetchone()[0]
    return task, members, logs


def _member_id(tid, user):
    return next(m["id"] for m in ts.get_task(tid)["members"] if m["user_id"] == user["id"])


# Mỗi thao tác: (hàm kiểm quyền(actor, task, w, tid), hàm gọi dịch vụ(actor, tid, w))
def _ops():
    return {
        "edit": (
            lambda a, t, w, tid: perm.can_edit_task(a, t),
            lambda a, tid, w: ts.update_task(
                a, tid, title="Tên mới", description=None, deadline=_future(12), lead_id=w["lead"]["id"]
            ),
        ),
        "submit": (
            lambda a, t, w, tid: perm.can_submit_task(a, t),
            lambda a, tid, w: ts.submit_task(a, tid),
        ),
        "approve": (
            lambda a, t, w, tid: perm.can_decide_task(a, t),
            lambda a, tid, w: ts.approve_task(a, tid),
        ),
        "reject": (
            lambda a, t, w, tid: perm.can_decide_task(a, t),
            lambda a, tid, w: ts.reject_task(a, tid, "Lý do từ chối"),
        ),
        "add_member": (
            lambda a, t, w, tid: perm.can_edit_allocation(a, t),
            lambda a, tid, w: ts.add_member(a, tid, w["extra"]["id"], "Phần mới", 5, "bản"),
        ),
        "update_member": (
            lambda a, t, w, tid: perm.can_edit_allocation(a, t),
            lambda a, tid, w: ts.update_member(a, _member_id(tid, w["member"]), "Nội dung sửa", 12, "bản"),
        ),
        "remove_member": (
            lambda a, t, w, tid: perm.can_edit_allocation(a, t),
            lambda a, tid, w: ts.remove_member(a, _member_id(tid, w["member"])),
        ),
        "progress": (
            lambda a, t, w, tid: perm.can_update_progress(
                a, t, next(m for m in t["members"] if m["user_id"] == w["member"]["id"])
            ),
            lambda a, tid, w: ts.update_progress(a, _member_id(tid, w["member"]), 3, None),
        ),
        "comment": (
            lambda a, t, w, tid: perm.can_comment_member(a, t),
            lambda a, tid, w: ts.comment_member(a, _member_id(tid, w["member"]), "Nhận xét"),
        ),
        "report": (
            lambda a, t, w, tid: perm.can_report_completion(a, t),
            lambda a, tid, w: ts.report_completion(a, tid, "Tóm tắt"),
        ),
        "evaluate": (
            lambda a, t, w, tid: perm.can_evaluate(a, t),
            lambda a, tid, w: ts.evaluate_task(a, tid, config.EVAL_PASS, "Ok"),
        ),
        "reopen": (
            lambda a, t, w, tid: perm.can_reopen(a, t),
            lambda a, tid, w: ts.reopen_task(a, tid, "Làm lại"),
        ),
    }


OPS = _ops()


@pytest.mark.parametrize("status", STATUSES_ORDER)
@pytest.mark.parametrize("op", sorted(OPS))
def test_permission_matrix(world, status, op):
    allowed_fn, call = OPS[op]
    for actor_key in ACTORS:
        actor = world[actor_key]
        tid = _build(world, status)
        assert ts.get_task(tid)["status"] == status
        task = ts.get_task(tid)
        allowed = allowed_fn(actor, task, world, tid)
        before = _snapshot(tid)
        label = f"{op}/{status}/{actor_key}"
        if allowed:
            try:
                call(actor, tid, world)
            except perm.PermissionDenied:
                pytest.fail(f"{label}: hàm kiểm quyền cho phép nhưng dịch vụ từ chối")
            except ValueError:
                pass  # lỗi nghiệp vụ (dữ liệu/điều kiện) không phải lỗi phân quyền
        else:
            with pytest.raises((perm.PermissionDenied, ValueError)):
                call(actor, tid, world)
            assert _snapshot(tid) == before, f"{label}: bị từ chối nhưng dữ liệu vẫn đổi"


def test_admin_has_no_task_permissions(world):
    """ADMIN chỉ quản lý tài khoản: mọi thao tác công việc đều bị từ chối ở mọi trạng thái."""
    admin = world["admin"]
    assert perm.can_manage_accounts(admin)
    assert not perm.can_create_task(admin, config.TYPE_DIRECT)
    assert not perm.can_create_task(admin, config.TYPE_TEAM)
    for status in STATUSES_ORDER:
        task = ts.get_task(_build(world, status))
        for name, predicate in (
            ("edit", perm.can_edit_task),
            ("submit", perm.can_submit_task),
            ("decide", perm.can_decide_task),
            ("allocation", perm.can_edit_allocation),
            ("comment", perm.can_comment_member),
            ("report", perm.can_report_completion),
            ("evaluate", perm.can_evaluate),
            ("reopen", perm.can_reopen),
        ):
            assert not predicate(admin, task), f"ADMIN không được {name} ở {status}"


def test_staff_cannot_create_direct_or_manage_accounts(world):
    staff = world["proposer"]
    assert not perm.can_create_task(staff, config.TYPE_DIRECT)
    assert perm.can_create_task(staff, config.TYPE_TEAM)
    assert not perm.can_manage_accounts(staff)
    assert not perm.can_manage_accounts(world["manager"])
    with pytest.raises(perm.PermissionDenied):
        ts.create_task(staff, config.TYPE_DIRECT, "Việc", None, _future(),
                       lead_id=world["member"]["id"], members=[{"content": "P"}])


def test_unauthenticated_and_unknown_type_denied():
    assert not perm.can_create_task(None, config.TYPE_TEAM)
    assert not perm.can_create_task({"id": 1, "role": config.ROLE_MANAGER}, "KHAC")
    assert not perm.can_manage_accounts(None)


def test_each_privileged_action_is_reachable_by_someone(world):
    """Chống ma trận 'rỗng': với mỗi thao tác phải có ít nhất một tổ hợp được phép."""
    for op, (allowed_fn, _) in OPS.items():
        reachable = False
        for status in STATUSES_ORDER:
            tid = _build(world, status)
            task = ts.get_task(tid)
            if any(allowed_fn(world[a], task, world, tid) for a in ACTORS):
                reachable = True
                break
        assert reachable, f"Thao tác {op} không ai thực hiện được"


def test_member_cannot_update_someone_elses_progress(world):
    tid = _build(world, config.STATUS_IN_PROGRESS)
    lead_part = _member_id(tid, world["lead"]) if any(
        m["user_id"] == world["lead"]["id"] for m in ts.get_task(tid)["members"]
    ) else None
    target = lead_part or _member_id(tid, world["member"])
    for key in ("outsider", "proposer", "manager", "admin"):
        before = _snapshot(tid)
        with pytest.raises((perm.PermissionDenied, ValueError)):
            ts.update_progress(world[key], target, 5, None)
        assert _snapshot(tid) == before


# --- Việc giao trực tiếp: một người được giao, người đó là chủ trì -------------------------
DIRECT_STATUSES = [
    config.STATUS_IN_PROGRESS,
    config.STATUS_REPORTED,
    config.STATUS_PASSED,
    config.STATUS_FAILED,
]


def _build_direct(w, status):
    """Việc giao trực tiếp cho w['member'] (cũng là chủ trì), đưa tới đúng trạng thái yêu cầu."""
    tid = ts.create_task(
        w["manager"], config.TYPE_DIRECT, "Việc giao", None, _future(),
        lead_id=w["member"]["id"], members=[{"content": "Làm", "quantity": 4, "unit": "bản"}],
    )
    if status == config.STATUS_IN_PROGRESS:
        return tid
    ts.update_progress(w["member"], _member_id(tid, w["member"]), 4, "Xong")  # tự chuyển sang Đã báo cáo
    if status == config.STATUS_REPORTED:
        return tid
    result = config.EVAL_PASS if status == config.STATUS_PASSED else config.EVAL_FAIL
    ts.evaluate_task(w["manager"], tid, result, "Nhận xét")
    return tid


def _direct_ops():
    ops = dict(OPS)
    ops["edit"] = (
        OPS["edit"][0],
        lambda a, tid, w: ts.update_task(
            a, tid, title="Tên mới", description=None, deadline=_future(12), lead_id=w["member"]["id"]
        ),
    )
    return ops


@pytest.mark.parametrize("status", DIRECT_STATUSES)
@pytest.mark.parametrize("op", sorted(OPS))
def test_permission_matrix_direct_task(world, status, op):
    allowed_fn, call = _direct_ops()[op]
    for actor_key in ("member", "lead", "proposer", "outsider", "manager", "admin"):
        actor = world[actor_key]
        tid = _build_direct(world, status)
        task = ts.get_task(tid)
        assert (task["task_type"], task["status"], task["lead_id"]) == (
            config.TYPE_DIRECT, status, world["member"]["id"],
        )
        allowed = allowed_fn(actor, task, world, tid)
        before = _snapshot(tid)
        label = f"direct/{op}/{status}/{actor_key}"
        if allowed:
            try:
                call(actor, tid, world)
            except perm.PermissionDenied:
                pytest.fail(f"{label}: hàm kiểm quyền cho phép nhưng dịch vụ từ chối")
            except ValueError:
                pass
        else:
            with pytest.raises((perm.PermissionDenied, ValueError)):
                call(actor, tid, world)
            assert _snapshot(tid) == before, f"{label}: bị từ chối nhưng dữ liệu vẫn đổi"


def test_direct_task_assignee_has_no_management_rights_over_own_task(world):
    tid = _build_direct(world, config.STATUS_IN_PROGRESS)
    task = ts.get_task(tid)
    assignee = world["member"]
    assert perm.is_lead(assignee, task)
    assert perm.can_update_progress(assignee, task, task["members"][0])
    for predicate in (perm.can_edit_task, perm.can_edit_allocation, perm.can_comment_member,
                      perm.can_decide_task, perm.can_evaluate, perm.can_submit_task, perm.can_report_completion):
        assert not predicate(assignee, task)
    assert perm.can_edit_task(world["manager"], task) and perm.can_edit_allocation(world["manager"], task)


def test_direct_task_cannot_gain_extra_people_even_for_manager(world):
    tid = _build_direct(world, config.STATUS_IN_PROGRESS)
    before = _snapshot(tid)
    for call in (
        lambda: ts.add_member(world["manager"], tid, world["extra"]["id"], "Thêm"),
        lambda: ts.add_members_bulk(world["manager"], tid, "Việc", None, [{"user_id": world["extra"]["id"]}]),
        lambda: ts.remove_member(world["manager"], _member_id(tid, world["member"])),
        lambda: ts.update_task(world["manager"], tid, title="T", description=None, deadline=_future(),
                               lead_id=world["extra"]["id"]),
    ):
        with pytest.raises(ValueError):
            call()
    assert _snapshot(tid) == before
