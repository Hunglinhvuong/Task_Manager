"""Giao diện Giai đoạn 3: Công việc của tôi, cập nhật tiến độ, báo cáo, đánh giá."""
from datetime import timedelta

from streamlit.testing.v1 import AppTest

import config
from core import auth, task_service as ts
from core.timeutil import today_vn

APP_FILE = str(config.BASE_DIR / "app.py")
PASSWORD = "MatKhau@123"


def _future(days=7):
    return (today_vn() + timedelta(days=days)).isoformat()


def _make_users():
    def make(username, role):
        return auth.get_user(auth.bootstrap_user(username, f"Tên {username}", PASSWORD, role))

    return {
        "manager": make("tpx", config.ROLE_MANAGER),
        "a": make("nv_a", config.ROLE_STAFF),
        "b": make("nv_b", config.ROLE_STAFF),
        "admin": make("quantri", config.ROLE_ADMIN),
    }


def _login(username, page):
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at.text_input[0].set_value(username)
    at.text_input[1].set_value(PASSWORD)
    at.button[0].click()
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception
    return at


def _submit(at, label):
    next(b for b in at.get("form_submit_button") if b.label == label).click()
    at.run()
    assert not at.exception


def _team_running(users):
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Việc nhóm", None, _future(), lead_id=users["a"]["id"])
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B", 4, "báo cáo")
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    return task_id


def _member(task_id, user):
    return next(m for m in ts.get_task(task_id)["members"] if m["user_id"] == user["id"])


def _finish(users, task_id, key, report):
    """Thành viên hoàn thành đủ khối lượng được giao (hoặc 100% nếu phần việc không có khối lượng)."""
    member = _member(task_id, users[key])
    value = member["quantity"] if ts.has_quantity(member["quantity"]) else 100
    return ts.update_progress(users[key], member["id"], value, report)


def test_my_tasks_empty_state(db_path):
    _make_users()
    at = _login("nv_b", "Công việc của tôi")
    assert at.header[0].value == "Công việc của tôi"
    assert len(at.success) == 1


def _click(at, label):
    next(b for b in at.button if b.label == label).click()
    at.run()
    assert not at.exception


def _done_input(at):
    return next(n for n in at.number_input if n.label.startswith("Khối lượng đã hoàn thành"))


def test_my_tasks_member_enters_absolute_quantity_and_percent_is_computed(db_path):
    users = _make_users()
    task_id = _team_running(users)  # B được giao 4 báo cáo
    at = _login("nv_b", "Công việc của tôi")
    assert at.main.subheader[0].value == "Phần việc của tôi (1)"
    assert "trên tổng 4 báo cáo" in _done_input(at).label
    _done_input(at).set_value(3).run()
    # Chương trình tự tính tiến độ % hiển thị ngay khi nhập (chưa cần lưu)
    assert any("Tiến độ tự tính: 75%" in str(p.proto.text) for p in at.get("progress"))
    next(t for t in at.text_area if t.label == "Báo cáo kết quả").set_value("Đã xong 3 báo cáo")
    _click(at, "Cập nhật tiến độ")
    assert not at.error
    member = _member(task_id, users["b"])
    assert (member["done_quantity"], member["progress"], member["result_report"]) == (3.0, 75, "Đã xong 3 báo cáo")


def test_my_tasks_full_quantity_requires_report(db_path):
    users = _make_users()
    task_id = _team_running(users)
    at = _login("nv_b", "Công việc của tôi")
    _done_input(at).set_value(4)
    _click(at, "Cập nhật tiến độ")  # đủ khối lượng = 100% nhưng chưa có báo cáo
    assert len(at.error) == 1
    assert _member(task_id, users["b"])["progress"] == 0


def test_my_tasks_percent_input_when_part_has_no_quantity(db_path):
    users = _make_users()
    task_id = ts.create_task(users["a"], config.TYPE_TEAM, "Không khối lượng", None, _future(), lead_id=users["a"]["id"])
    ts.add_member(users["a"], task_id, users["b"]["id"], "Việc không đo được")
    ts.submit_task(users["a"], task_id)
    ts.approve_task(users["manager"], task_id)
    at = _login("nv_b", "Công việc của tôi")
    assert not any(n.label.startswith("Khối lượng đã hoàn thành") for n in at.number_input)
    next(n for n in at.number_input if n.label == "Tiến độ (%)").set_value(45)
    _click(at, "Cập nhật tiến độ")
    assert not at.error
    member = _member(task_id, users["b"])
    assert (member["progress"], member["done_quantity"]) == (45, None)


def test_my_tasks_lead_sees_leading_section_and_no_progress_form(db_path):
    users = _make_users()
    _team_running(users)
    at = _login("nv_a", "Công việc của tôi")
    assert [s.value for s in at.main.subheader] == [
        "Phần việc của tôi (0)",
        "Công việc tôi chủ trì (1)",
        "Công việc tôi đề xuất (0)",
    ]
    assert not any(b.label == "Cập nhật tiến độ" for b in at.button)


def test_my_tasks_admin_has_nothing(db_path):
    _make_users()
    at = _login("quantri", "Công việc của tôi")
    assert len(at.success) == 1


def test_lead_reports_completion_then_manager_evaluates_via_ui(db_path):
    users = _make_users()
    task_id = _team_running(users)
    _finish(users, task_id, "b", "B xong")

    # Chủ trì báo cáo hoàn thành trong chi tiết công việc
    at = _login("nv_a", "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert not at.exception
    next(t for t in at.text_area if t.label == "Báo cáo tổng hợp").set_value("Hoàn thành toàn bộ")
    _submit(at, "Báo cáo hoàn thành")
    assert not at.error
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED

    # Trưởng phòng đánh giá ở tab "Chờ đánh giá" của trang Phê duyệt
    at = _login("tpx", "Phê duyệt")
    assert any(t.label == "Chờ đánh giá (1)" for t in at.tabs)
    next(t for t in at.text_area if t.label == "Nhận xét đánh giá").set_value("Đạt yêu cầu")
    _submit(at, "Lưu đánh giá")
    assert not at.error
    task = ts.get_task(task_id)
    assert (task["status"], task["eval_result"], task["eval_comment"]) == (
        config.STATUS_PASSED,
        config.EVAL_PASS,
        "Đạt yêu cầu",
    )


def test_evaluate_fail_without_comment_shows_error_then_reopen_via_ui(db_path):
    users = _make_users()
    task_id = _team_running(users)
    _finish(users, task_id, "b", "B xong")
    ts.report_completion(users["a"], task_id, "Xong")

    at = _login("tpx", "Phê duyệt")
    # Widget trong form chỉ được ghi nhận khi bấm gửi, nên không chạy lại giữa chừng.
    next(r for r in at.radio if r.label == "Kết quả đánh giá").set_value(config.EVAL_FAIL)
    _submit(at, "Lưu đánh giá")  # thiếu nhận xét
    assert len(at.error) == 1
    assert ts.get_task(task_id)["status"] == config.STATUS_REPORTED

    next(r for r in at.radio if r.label == "Kết quả đánh giá").set_value(config.EVAL_FAIL)
    next(t for t in at.text_area if t.label == "Nhận xét đánh giá").set_value("Thiếu số liệu")
    _submit(at, "Lưu đánh giá")
    assert ts.get_task(task_id)["status"] == config.STATUS_FAILED

    # Mở lại từ chi tiết công việc
    at = _login("tpx", "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    next(t for t in at.text_area if t.label == "Lý do mở lại (bắt buộc)").set_value("Làm lại phần B")
    _submit(at, "Mở lại công việc để làm tiếp")
    assert not at.error
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS


def test_staff_sees_pending_evaluations_but_no_controls(db_path):
    users = _make_users()
    task_id = _team_running(users)
    _finish(users, task_id, "b", "B xong")
    ts.report_completion(users["a"], task_id, "Xong")
    at = _login("nv_b", "Phê duyệt")
    assert not any(b.label == "Lưu đánh giá" for b in at.get("form_submit_button"))
    assert not any(t.label == "Nhận xét đánh giá" for t in at.text_area)


def test_detail_shows_own_progress_form_and_lead_comment_form(db_path):
    users = _make_users()
    task_id = _team_running(users)
    # Thành viên B thấy form tiến độ, không thấy form nhận xét
    at = _login("nv_b", "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert any(b.label == "Cập nhật tiến độ" for b in at.button)
    assert "Lưu nhận xét" not in {b.label for b in at.get("form_submit_button")}
    # Chủ trì A thấy form nhận xét, không có form tiến độ (A không có phần việc)
    at = _login("nv_a", "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert "Lưu nhận xét" in {b.label for b in at.get("form_submit_button")}
    assert not any(b.label == "Cập nhật tiến độ" for b in at.button)
    next(t for t in at.text_area if t.label == "Nhận xét").set_value("Làm tốt lắm")
    _submit(at, "Lưu nhận xét")
    assert not at.error
    assert _member(task_id, users["b"])["lead_comment"] == "Làm tốt lắm"


def test_log_tab_describes_phase3_actions(db_path):
    users = _make_users()
    task_id = _team_running(users)
    _finish(users, task_id, "b", "B xong")
    ts.report_completion(users["a"], task_id, "Xong")
    ts.evaluate_task(users["manager"], task_id, config.EVAL_FAIL, "Chưa đạt")
    at = _login("nv_a", "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert not at.exception
    from ui import components as c
    from core import log_service

    texts = {l["action"]: c.describe_log(l["action"], l["detail"]) for l in log_service.get_logs(task_id)}
    assert "100%" in texts[config.LOG_MEMBER_PROGRESS]
    assert "Báo cáo tổng hợp: Xong" == texts[config.LOG_REPORTED]
    assert texts[config.LOG_EVALUATED] == "Kết quả: Không đạt; nhận xét: Chưa đạt"
