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
        "admin": make("admin", config.ROLE_ADMIN),
        "manager": make("tpx", config.ROLE_MANAGER),
        "a": make("nv_a", config.ROLE_STAFF),
        "b": make("nv_b", config.ROLE_STAFF),
        "c": make("nv_c", config.ROLE_STAFF),
    }


def _team_running(users):
    """Việc nhóm đang thực hiện: chủ trì A, B và C mỗi người một phần."""
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Việc nhóm", None, _future(), lead_id=users["a"]["id"])
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B", 5, "báo cáo")
    ts.add_member(users["a"], task_id, users["c"]["id"], "Phần C", 3, "báo cáo")
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    return task_id


def _direct_running(users, key="a"):
    """Giao trực tiếp cho một người (người đó là chủ trì)."""
    return ts.create_task(
        users["manager"],
        config.TYPE_DIRECT,
        "Việc giao",
        None,
        _future(),
        lead_id=users[key]["id"],
        members=[{"content": "Làm"}],
    )


def _member(task_id, user):
    return next(m for m in ts.get_task(task_id)["members"] if m["user_id"] == user["id"])


def _finish_all(users, task_id, keys=("b", "c")):
    for k in keys:
        _finish(users, task_id, k, f"Xong phần {k}")


def _finish(users, task_id, key, report):
    """Thành viên hoàn thành đủ khối lượng được giao (hoặc 100% nếu phần việc không có khối lượng)."""
    member = _member(task_id, users[key])
    value = member["quantity"] if ts.has_quantity(member["quantity"]) else 100
    return ts.update_progress(users[key], member["id"], value, report)


def _actions(task_id):
    return [l["action"] for l in log_service.get_logs(task_id)]


# --- Cập nhật tiến độ ---------------------------------------------------------
def test_member_updates_own_progress_and_logs(users):
    task_id = _team_running(users)  # B được giao 5 báo cáo
    mid = _member(task_id, users["b"])["id"]
    assert ts.update_progress(users["b"], mid, 2, "Đã làm 2 báo cáo") is True
    member = _member(task_id, users["b"])
    assert (member["done_quantity"], member["progress"], member["result_report"]) == (2.0, 40, "Đã làm 2 báo cáo")
    log = log_service.get_logs(task_id)[-1]
    assert log["action"] == config.LOG_MEMBER_PROGRESS
    assert log["member_id"] == mid
    assert log["detail"]["done_quantity"] == {"old": 0.0, "new": 2.0}
    assert log["detail"]["progress"] == {"old": 0, "new": 40}
    assert (log["detail"]["quantity"], log["detail"]["unit"]) == (5.0, "báo cáo")
    # Gửi lại đúng giá trị cũ: không thay đổi, không ghi nhật ký
    count = len(log_service.get_logs(task_id))
    assert ts.update_progress(users["b"], mid, 2, "Đã làm 2 báo cáo") is False
    assert len(log_service.get_logs(task_id)) == count
    # Cho phép giảm khối lượng đã hoàn thành
    assert ts.update_progress(users["b"], mid, 1, None) is True
    assert _member(task_id, users["b"])["progress"] == 20


def test_only_the_member_can_update_their_own_part(users):
    task_id = _team_running(users)
    mid_b = _member(task_id, users["b"])["id"]
    for actor in (users["a"], users["c"], users["manager"], users["admin"]):
        with pytest.raises(permissions.PermissionDenied):
            ts.update_progress(actor, mid_b, 10, None)
    assert _member(task_id, users["b"])["progress"] == 0


def test_progress_validation_by_quantity(users):
    task_id = _team_running(users)  # B được giao 5 báo cáo
    mid = _member(task_id, users["b"])["id"]
    for bad in (-1, "abc", None, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ts.update_progress(users["b"], mid, bad, "x")
    with pytest.raises(ValueError):  # đạt đủ khối lượng (100%) bắt buộc có báo cáo
        ts.update_progress(users["b"], mid, 5, "   ")
    with pytest.raises(ValueError):
        ts.update_progress(users["b"], mid, 1, "x" * 4001)
    assert (_member(task_id, users["b"])["done_quantity"], _member(task_id, users["b"])["progress"]) == (0.0, 0)
    assert ts.update_progress(users["b"], mid, "3", "ok") is True  # số dạng chuỗi hợp lệ
    assert ts.update_progress(users["b"], mid, 1.5, "ok") is True  # được phép số thập phân
    assert _member(task_id, users["b"])["progress"] == 30


def test_progress_validation_by_percent_when_no_quantity(users):
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Không khối lượng", None, _future(), lead_id=users["a"]["id"])
    mid = ts.add_member(users["a"], task_id, users["b"]["id"], "Việc không đo được")
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    for bad in (-1, 101, 50.5, "abc", None, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ts.update_progress(users["b"], mid, bad, "x")
    with pytest.raises(ValueError):
        ts.update_progress(users["b"], mid, 100, "   ")
    assert ts.update_progress(users["b"], mid, "70", "ok") is True
    member = _member(task_id, users["b"])
    assert (member["progress"], member["done_quantity"]) == (70, None)


def test_progress_blocked_unless_in_progress(users):
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Nháp", None, _future(), lead_id=users["a"]["id"])
    mid = ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B")
    with pytest.raises(ValueError):  # còn là bản nháp
        ts.update_progress(users["b"], mid, 10, None)
    with pytest.raises(ValueError):
        ts.update_progress(users["b"], 9999, 10, None)


def test_manager_member_can_update_own_part(users):
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Có TP", None, _future(), lead_id=users["a"]["id"])
    mid = ts.add_member(users["a"], task_id, users["manager"]["id"], "Phần của TP")
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    assert ts.update_progress(users["manager"], mid, 50, None) is True


# --- Nhận xét ------------------------------------------------------------------
def test_comment_by_lead_and_manager_only(users):
    task_id = _team_running(users)
    mid = _member(task_id, users["b"])["id"]
    assert ts.comment_member(users["a"], mid, "Làm tốt") is True
    assert _member(task_id, users["b"])["lead_comment"] == "Làm tốt"
    assert ts.comment_member(users["manager"], mid, "Cần bổ sung số liệu") is True
    for actor in (users["b"], users["c"], users["admin"]):
        with pytest.raises(permissions.PermissionDenied):
            ts.comment_member(actor, mid, "Không được")
    log = log_service.get_logs(task_id)[-1]
    assert log["action"] == config.LOG_MEMBER_COMMENT
    assert log["detail"]["comment"] == {"old": "Làm tốt", "new": "Cần bổ sung số liệu"}


def test_comment_noop_clear_and_status_rules(users):
    task_id = _team_running(users)
    mid = _member(task_id, users["b"])["id"]
    ts.comment_member(users["a"], mid, "Nhận xét")
    assert ts.comment_member(users["a"], mid, "Nhận xét") is False
    assert ts.comment_member(users["a"], mid, "  ") is True  # xóa nhận xét
    assert _member(task_id, users["b"])["lead_comment"] is None
    with pytest.raises(ValueError):
        ts.comment_member(users["a"], mid, "x" * 1001)
    # Bản nháp thì chưa nhận xét được
    draft = ts.create_task(users["a"], config.TYPE_TEAM, "Nháp", None, _future(), lead_id=users["a"]["id"])
    dmid = ts.add_member(users["a"], draft, users["b"]["id"], "Phần")
    with pytest.raises(ValueError):
        ts.comment_member(users["a"], dmid, "Sớm quá")


def test_lead_of_another_task_cannot_comment(users):
    task_id = _team_running(users)
    other = ts.create_task(users["b"], config.TYPE_TEAM, "Việc khác", None, _future(), lead_id=users["b"]["id"])
    ts.add_member(users["b"], other, users["c"]["id"], "Phần")
    ts.submit_task(users["b"], other)
    ts.approve_task(users["manager"], other)
    with pytest.raises(permissions.PermissionDenied):  # B chủ trì việc khác, không phải việc này
        ts.comment_member(users["b"], _member(task_id, users["c"])["id"], "Không được")


# --- Báo cáo hoàn thành (việc nhóm) ----------------------------------------------
def test_report_completion_requires_all_parts_done(users):
    task_id = _team_running(users)
    with pytest.raises(ValueError):
        ts.report_completion(users["a"], task_id, "Tổng hợp")
    _finish(users, task_id, "b", "B xong")
    with pytest.raises(ValueError) as err:  # C chưa xong
        ts.report_completion(users["a"], task_id, "Tổng hợp")
    assert "Tên nv_c" in str(err.value)
    ts.update_progress(users["c"], _member(task_id, users["c"])["id"], 2, "gần xong")  # 2/3 = 67%
    with pytest.raises(ValueError):
        ts.report_completion(users["a"], task_id, "Tổng hợp")
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS


def test_report_completion_flow_and_permissions(users):
    task_id = _team_running(users)
    _finish_all(users, task_id)
    for actor in (users["b"], users["manager"], users["admin"]):  # chỉ chủ trì
        with pytest.raises(permissions.PermissionDenied):
            ts.report_completion(actor, task_id, "Tổng hợp")
    with pytest.raises(ValueError):
        ts.report_completion(users["a"], task_id, "   ")
    ts.report_completion(users["a"], task_id, "Đã hoàn thành đầy đủ")
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_REPORTED
    assert task["report_summary"] == "Đã hoàn thành đầy đủ"
    assert task["completed_at"]
    log = log_service.get_logs(task_id)[-1]
    assert (log["action"], log["detail"]["auto"]) == (config.LOG_REPORTED, False)
    with pytest.raises(ValueError):  # không báo cáo hai lần
        ts.report_completion(users["a"], task_id, "Lần hai")


def test_progress_locked_after_report(users):
    task_id = _team_running(users)
    _finish_all(users, task_id)
    ts.report_completion(users["a"], task_id, "Xong")
    with pytest.raises(ValueError):
        ts.update_progress(users["b"], _member(task_id, users["b"])["id"], 2, "đổi ý")
    # Nhưng vẫn nhận xét được khi chờ đánh giá
    assert ts.comment_member(users["manager"], _member(task_id, users["b"])["id"], "Tốt")


def test_report_completion_not_for_direct_tasks(users):
    task_id = _direct_running(users)
    with pytest.raises(ValueError):
        ts.report_completion(users["manager"], task_id, "Tổng hợp")
    with pytest.raises(ValueError):  # người được giao (chủ trì) cũng không báo cáo tổng hợp: hệ thống tự báo cáo
        ts.report_completion(users["a"], task_id, "Tổng hợp")


# --- Việc DIRECT: tự báo cáo khi người được giao hoàn thành -------------------------
def test_direct_auto_reports_when_assignee_finishes(users):
    task_id = _direct_running(users)
    member_id = _member(task_id, users["a"])["id"]
    ts.update_progress(users["a"], member_id, 60, None)
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS  # chưa xong
    _finish(users, task_id, "a", "A xong")
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_REPORTED
    assert task["completed_at"]
    assert _actions(task_id)[-2:] == [config.LOG_MEMBER_PROGRESS, config.LOG_REPORTED]
    assert log_service.get_logs(task_id)[-1]["detail"] == {"auto": True}


def test_direct_single_member_reports_immediately(users):
    task_id = _direct_running(users)
    _finish(users, task_id, "a", "Xong")
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED


def test_team_never_auto_reports(users):
    task_id = _team_running(users)
    _finish_all(users, task_id)
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS


# --- Đánh giá --------------------------------------------------------------------
def _reported_team(users):
    task_id = _team_running(users)
    _finish_all(users, task_id)
    ts.report_completion(users["a"], task_id, "Xong")
    return task_id


def test_evaluate_pass(users):
    task_id = _reported_team(users)
    for actor in (users["a"], users["b"], users["admin"]):
        with pytest.raises(permissions.PermissionDenied):
            ts.evaluate_task(actor, task_id, config.EVAL_PASS, None)
    ts.evaluate_task(users["manager"], task_id, config.EVAL_PASS, "Đạt yêu cầu")
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_PASSED
    assert (task["eval_result"], task["eval_comment"]) == (config.EVAL_PASS, "Đạt yêu cầu")
    assert task["evaluated_by"] == users["manager"]["id"]
    assert task["evaluated_at"]
    log = log_service.get_logs(task_id)[-1]
    assert (log["action"], log["detail"]["result"]) == (config.LOG_EVALUATED, config.EVAL_PASS)
    with pytest.raises(ValueError):  # đã đánh giá
        ts.evaluate_task(users["manager"], task_id, config.EVAL_FAIL, "Đổi ý")


def test_evaluate_pass_comment_optional_fail_comment_required(users):
    t1 = _reported_team(users)
    ts.evaluate_task(users["manager"], t1, config.EVAL_PASS, None)
    t2 = _reported_team(users)
    with pytest.raises(ValueError):
        ts.evaluate_task(users["manager"], t2, config.EVAL_FAIL, "  ")
    with pytest.raises(ValueError):
        ts.evaluate_task(users["manager"], t2, "MAYBE", "x")
    assert ts.get_task(t2)["status"] == config.STATUS_REPORTED
    ts.evaluate_task(users["manager"], t2, config.EVAL_FAIL, "Thiếu số liệu")
    assert ts.get_task(t2)["status"] == config.STATUS_FAILED


def test_cannot_evaluate_unreported_task(users):
    task_id = _team_running(users)
    with pytest.raises(ValueError):
        ts.evaluate_task(users["manager"], task_id, config.EVAL_PASS, None)


def test_manager_can_evaluate_direct_task(users):
    task_id = _direct_running(users)
    _finish(users, task_id, "a", "Xong")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_PASS, "Tốt")
    assert ts.get_task(task_id)["status"] == config.STATUS_PASSED


# --- Mở lại việc "Không đạt" ------------------------------------------------------
def test_reopen_failed_task_and_complete_again(users):
    task_id = _reported_team(users)
    ts.evaluate_task(users["manager"], task_id, config.EVAL_FAIL, "Làm lại phần C")
    with pytest.raises(ValueError):
        ts.reopen_task(users["manager"], task_id, "   ")
    for actor in (users["a"], users["b"], users["admin"]):
        with pytest.raises(permissions.PermissionDenied):
            ts.reopen_task(actor, task_id, "Mở lại")
    ts.reopen_task(users["manager"], task_id, "Cần làm lại phần C")
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_IN_PROGRESS
    assert all(task[k] is None for k in ("eval_result", "eval_comment", "evaluated_by", "evaluated_at", "completed_at", "report_summary"))
    log = log_service.get_logs(task_id)[-1]
    assert log["action"] == config.LOG_REOPENED
    assert log["detail"]["previous_eval_comment"] == "Làm lại phần C"
    # Nhật ký vẫn giữ nguyên lần đánh giá Không đạt trước đó
    assert config.LOG_EVALUATED in _actions(task_id)

    # Làm lại: C sửa tiến độ/báo cáo, chủ trì báo cáo lại, trưởng phòng đánh giá Đạt
    mid_c = _member(task_id, users["c"])["id"]
    ts.update_progress(users["c"], mid_c, 3, "Đã làm lại phần C")  # đủ 3/3 khối lượng được giao
    ts.report_completion(users["a"], task_id, "Báo cáo lần 2")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_PASS, "Đạt")
    assert ts.get_task(task_id)["status"] == config.STATUS_PASSED


def test_cannot_reopen_passed_or_reported(users):
    task_id = _reported_team(users)
    with pytest.raises(ValueError):
        ts.reopen_task(users["manager"], task_id, "Chưa đánh giá")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_PASS, None)
    with pytest.raises(ValueError):
        ts.reopen_task(users["manager"], task_id, "Đã đạt")


def test_direct_reopen_then_member_reconfirms(users):
    task_id = _direct_running(users)
    mid = _member(task_id, users["a"])["id"]
    ts.update_progress(users["a"], mid, 100, "Xong")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_FAIL, "Làm lại")
    ts.reopen_task(users["manager"], task_id, "Cần làm lại")
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS
    # Thành viên làm lại, gửi lại với báo cáo mới -> tự báo cáo lần nữa
    ts.update_progress(users["a"], mid, 100, "Đã làm lại xong")
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED
    # Gửi lại đúng giá trị cũ sau khi mở lại cũng kích hoạt tự báo cáo
    ts.evaluate_task(users["manager"], task_id, config.EVAL_FAIL, "Vẫn chưa đạt")
    ts.reopen_task(users["manager"], task_id, "Thử lại")
    assert ts.update_progress(users["a"], mid, 100, "Đã làm lại xong") is False
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED


# --- Truy vấn ----------------------------------------------------------------------
def test_pending_evaluations_list(users):
    t1 = _reported_team(users)
    t2 = _reported_team(users)
    assert [t["id"] for t in ts.list_pending_evaluations()] == [t1, t2]
    ts.evaluate_task(users["manager"], t1, config.EVAL_PASS, None)
    assert [t["id"] for t in ts.list_pending_evaluations()] == [t2]


def test_list_my_work_groups_by_role(users):
    running = _team_running(users)  # A chủ trì, B và C có phần
    draft = ts.create_task(users["b"], config.TYPE_TEAM, "Nháp của B", None, _future(), lead_id=users["b"]["id"])
    direct = _direct_running(users, "b")

    work_a = ts.list_my_work(users["a"]["id"])
    assert [t["id"] for t in work_a["leading"]] == [running]
    assert work_a["parts"] == []
    assert work_a["proposed"] == []  # việc đã được duyệt không còn ở mục "đề xuất"

    work_b = ts.list_my_work(users["b"]["id"])
    assert {p["task_id"] for p in work_b["parts"]} == {running, direct}
    assert [t["id"] for t in work_b["proposed"]] == [draft]
    assert {p["member_id"] for p in work_b["parts"]} == {_member(running, users["b"])["id"], _member(direct, users["b"])["id"]}

    assert ts.list_my_work(users["admin"]["id"]) == {"parts": [], "leading": [], "proposed": []}


def test_list_my_work_drops_finished_tasks_and_flags_overdue(users):
    task_id = _direct_running(users)
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET deadline = ? WHERE id = ?", ((today_vn() - timedelta(days=2)).isoformat(), task_id))
    part = ts.list_my_work(users["a"]["id"])["parts"][0]
    assert part["overdue"] is True
    ts.update_progress(users["a"], part["member_id"], 100, "Xong")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_PASS, None)
    assert ts.list_my_work(users["a"]["id"])["parts"] == []


def test_full_phase3_workflow_logged_in_order(users):
    task_id = _team_running(users)
    _finish(users, task_id, "b", "B xong")
    ts.comment_member(users["a"], _member(task_id, users["b"])["id"], "OK")
    _finish(users, task_id, "c", "C xong")
    ts.report_completion(users["a"], task_id, "Xong")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_FAIL, "Chưa đạt")
    ts.reopen_task(users["manager"], task_id, "Làm lại")
    assert _actions(task_id)[-6:] == [
        config.LOG_MEMBER_PROGRESS,
        config.LOG_MEMBER_COMMENT,
        config.LOG_MEMBER_PROGRESS,
        config.LOG_REPORTED,
        config.LOG_EVALUATED,
        config.LOG_REOPENED,
    ]
    with database.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM task_logs WHERE task_id = ?", (task_id,))
