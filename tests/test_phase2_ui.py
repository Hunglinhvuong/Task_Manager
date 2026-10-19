"""Kiểm thử giao diện Giai đoạn 2 bằng AppTest: tạo, gửi duyệt, duyệt/từ chối, phân công."""
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
    }


def _login(username):
    at = AppTest.from_file(APP_FILE, default_timeout=30).run()
    at.text_input[0].set_value(username)
    at.text_input[1].set_value(PASSWORD)
    at.button[0].click()
    at.run()
    assert not at.exception
    return at


def _goto(at, page):
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception
    return at


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_task_list_renders_for_every_role(db_path):
    users = _make_users()
    ts.create_task(
        users["manager"],
        config.TYPE_DIRECT,
        "Việc giao",
        None,
        _future(),
        lead_id=users["a"]["id"],
        members=[{"content": "Phần A"}],
    )
    for username in ("tpx", "nv_a"):
        at = _goto(_login(username), "Danh sách công việc")
        assert at.header[0].value == "Danh sách công việc"
        assert len(at.dataframe) >= 1


def test_staff_creates_team_task_via_ui(db_path):
    users = _make_users()
    at = _goto(_login("nv_a"), "Danh sách công việc")
    assert list(at.radio(key="new_type").options) == [
        config.TASK_TYPE_LABELS[config.TYPE_TEAM],
        config.TASK_TYPE_LABELS[config.TYPE_PERSONAL],
    ]
    at.text_input[0].set_value("Việc nhóm từ giao diện")
    next(b for b in at.button if b.label == "Tạo công việc").click()
    at.run()
    assert not at.exception
    assert not at.error
    tasks = ts.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Việc nhóm từ giao diện"
    assert tasks[0]["status"] == config.STATUS_DRAFT
    assert tasks[0]["proposer_name"] == "Tên nv_a"


def test_staff_creates_personal_task_pending_approval_via_ui(db_path):
    users = _make_users()
    at = _goto(_login("nv_a"), "Danh sách công việc")
    at.radio(key="new_type").set_value(config.TYPE_PERSONAL).run()
    at.text_input[0].set_value("Việc cá nhân của nhân viên")
    next(t for t in at.text_area if t.label == "Nội dung phần việc cá nhân").set_value("Tự thực hiện")
    next(b for b in at.button if b.label == "Tạo công việc").click()
    at.run()

    assert not at.exception
    assert not at.error
    task = ts.get_task(ts.list_tasks()[0]["id"])
    assert task["task_type"] == config.TYPE_PERSONAL
    assert task["status"] == config.STATUS_PENDING_APPROVAL
    assert task["proposer_id"] == users["a"]["id"]
    assert task["lead_id"] == users["a"]["id"]
    assert task["members"][0]["user_id"] == users["a"]["id"]


def test_manager_creates_direct_task_via_ui(db_path):
    users = _make_users()
    at = _goto(_login("tpx"), "Danh sách công việc")
    assert set(at.radio(key="new_type").options) == {
        config.TASK_TYPE_LABELS[config.TYPE_TEAM],
        config.TASK_TYPE_LABELS[config.TYPE_DIRECT],
    }
    at.radio(key="new_type").set_value(config.TYPE_DIRECT).run()
    at.text_input[0].set_value("Việc giao trực tiếp")
    assert not any(m.label.startswith("Giao cho") for m in at.multiselect)  # chỉ chọn một người
    next(s for s in at.selectbox if s.label.startswith("Giao cho")).set_value(users["a"]["id"])
    at.text_area[1].set_value("Nội dung công việc giao")
    next(b for b in at.button if b.label == "Tạo công việc").click()
    at.run()
    assert not at.exception
    assert not at.error
    task = ts.list_tasks()[0]
    assert task["status"] == config.STATUS_IN_PROGRESS
    assert task["member_count"] == 1
    assert task["lead_name"] == "Tên nv_a"  # người được giao là chủ trì


def test_direct_create_without_assignee_shows_error(db_path):
    _make_users()
    at = _goto(_login("tpx"), "Danh sách công việc")
    at.radio(key="new_type").set_value(config.TYPE_DIRECT).run()
    at.text_input[0].set_value("Việc chưa chọn người")
    at.text_area[1].set_value("Nội dung")
    next(b for b in at.button if b.label == "Tạo công việc").click()
    at.run()
    assert len(at.error) == 1
    assert ts.list_tasks() == []


def test_create_without_title_shows_error(db_path):
    _make_users()
    at = _goto(_login("nv_a"), "Danh sách công việc")
    next(b for b in at.button if b.label == "Tạo công việc").click()
    at.run()
    assert len(at.error) == 1
    assert ts.list_tasks() == []


def test_approvals_page_manager_can_approve(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["a"], config.TYPE_TEAM, "Chờ duyệt", None, _future(), lead_id=users["a"]["id"]
    )
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B")
    ts.submit_task(users["a"], task_id)

    at = _goto(_login("tpx"), "Phê duyệt")
    _button(at, "Phê duyệt").click()
    at.run()
    assert not at.exception
    assert not at.error
    assert ts.get_task(task_id)["status"] == config.STATUS_IN_PROGRESS


def test_approvals_page_manager_can_reject_with_reason(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["a"], config.TYPE_TEAM, "Sẽ bị từ chối", None, _future(), lead_id=users["a"]["id"]
    )
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B")
    ts.submit_task(users["a"], task_id)

    at = _goto(_login("tpx"), "Phê duyệt")
    # Thiếu lý do thì báo lỗi
    _button(at, "Xác nhận từ chối").click()
    at.run()
    assert len(at.error) == 1
    assert ts.get_task(task_id)["status"] == config.STATUS_PENDING_APPROVAL

    at.text_area(key=f"reason_{task_id}").set_value("Chưa đủ khối lượng")
    _button(at, "Xác nhận từ chối").click()
    at.run()
    assert not at.exception
    task = ts.get_task(task_id)
    assert task["status"] == config.STATUS_REJECTED
    assert task["reject_reason"] == "Chưa đủ khối lượng"


def test_approvals_page_staff_sees_list_but_no_decision_buttons(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["a"], config.TYPE_TEAM, "Chờ duyệt", None, _future(), lead_id=users["a"]["id"]
    )
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B")
    ts.submit_task(users["a"], task_id)

    at = _goto(_login("nv_b"), "Phê duyệt")
    assert not at.exception
    labels = {b.label for b in at.button}
    assert "Phê duyệt" not in labels
    assert "Xác nhận từ chối" not in labels
    assert len(at.info) == 2  # mỗi tab (chờ phê duyệt, chờ đánh giá) có một dòng giải thích


def test_approvals_page_empty_state(db_path):
    _make_users()
    at = _goto(_login("tpx"), "Phê duyệt")
    assert not at.exception
    assert len(at.success) == 2  # trạng thái rỗng của cả hai tab


def test_task_detail_shows_tabs_and_submit_button(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["a"], config.TYPE_TEAM, "Có chi tiết", None, _future(), lead_id=users["a"]["id"]
    )
    ts.add_member(users["a"], task_id, users["b"]["id"], "Phần B", 2, "giờ")

    at = _goto(_login("nv_a"), "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Thông tin", "Phần việc", "Nhật ký"]
    _button(at, "Gửi phê duyệt").click()
    at.run()
    assert not at.exception
    assert not at.error
    assert ts.get_task(task_id)["status"] == config.STATUS_PENDING_APPROVAL


def test_other_staff_sees_no_edit_controls(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["a"], config.TYPE_TEAM, "Của A", None, _future(), lead_id=users["a"]["id"]
    )
    at = _goto(_login("nv_b"), "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert not at.exception
    labels = {b.label for b in at.button}
    assert "Gửi phê duyệt" not in labels
    assert "Thêm phần việc" not in {b.label for b in at.get("form_submit_button")}


def _open_task(username, task_id):
    at = _goto(_login(username), "Danh sách công việc")
    at.selectbox(key="task_picker").select(task_id).run()
    assert not at.exception
    return at


def _submit(at, label):
    next(b for b in at.get("form_submit_button") if b.label == label).click()
    at.run()
    assert not at.exception


def test_lead_adds_edits_and_removes_member_via_ui(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["manager"], config.TYPE_TEAM, "Phân công", None, _future(), lead_id=users["a"]["id"]
    )

    # Chủ trì thêm phần việc cho B
    at = _open_task("nv_a", task_id)
    next(sb for sb in at.selectbox if sb.label == "Thành viên").select(users["b"]["id"])
    form_areas = [t for t in at.text_area if t.label == "Nội dung phần việc"]
    form_areas[0].set_value("Phần của B")
    _submit(at, "Thêm phần việc")
    assert not at.error
    members = ts.get_task(task_id)["members"]
    assert len(members) == 1
    assert (members[0]["full_name"], members[0]["content"]) == ("Tên nv_b", "Phần của B")

    # Sửa phần việc
    at = _open_task("nv_a", task_id)
    edit_areas = [t for t in at.text_area if t.label == "Nội dung phần việc"]
    edit_areas[-1].set_value("Phần của B (đã sửa)")
    _submit(at, "Lưu phần việc")
    assert not at.error
    assert ts.get_task(task_id)["members"][0]["content"] == "Phần của B (đã sửa)"

    # Xóa phần việc (cần tích xác nhận)
    at = _open_task("nv_a", task_id)
    remove_button = next(b for b in at.button if b.label == "Xóa phần việc")
    assert remove_button.disabled
    next(cb for cb in at.checkbox if cb.label == "Xác nhận xóa phần việc này").check().run()
    next(b for b in at.button if b.label == "Xóa phần việc").click()
    at.run()
    assert not at.exception
    assert ts.get_task(task_id)["members"] == []


def test_bulk_table_adds_all_preselected_members(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["manager"], config.TYPE_TEAM, "Nhập bảng", None, _future(), lead_id=users["a"]["id"]
    )
    at = _open_task("nv_a", task_id)
    next(r for r in at.radio if r.label == "Cách nhập").set_value("Nhập bảng (nhiều người)").run()
    assert not at.exception
    next(cb for cb in at.checkbox if cb.label == "Chọn sẵn tất cả mọi người").check().run()
    next(t for t in at.text_area if t.label == "Nội dung phần việc (chung)").set_value("Kiểm tra trạm")
    next(t for t in at.text_input if t.label == "Đơn vị (chung)").set_value("trạm")
    _submit(at, "Thêm các phần việc đã chọn")
    assert not at.error
    members = ts.get_task(task_id)["members"]
    assert len(members) == 3  # trưởng phòng, nv_a, nv_b: mọi người có thể nhận việc
    assert {m["content"] for m in members} == {"Kiểm tra trạm"}
    assert {m["unit"] for m in members} == {"trạm"}


def test_bulk_table_requires_a_selection_and_content(db_path):
    users = _make_users()
    task_id = ts.create_task(
        users["manager"], config.TYPE_TEAM, "Nhập bảng", None, _future(), lead_id=users["a"]["id"]
    )
    at = _open_task("nv_a", task_id)
    next(r for r in at.radio if r.label == "Cách nhập").set_value("Nhập bảng (nhiều người)").run()
    next(t for t in at.text_area if t.label == "Nội dung phần việc (chung)").set_value("Việc")
    _submit(at, "Thêm các phần việc đã chọn")  # chưa tích ai
    assert len(at.error) == 1
    assert ts.get_task(task_id)["members"] == []

    next(cb for cb in at.checkbox if cb.label == "Chọn sẵn tất cả mọi người").check().run()
    next(t for t in at.text_area if t.label == "Nội dung phần việc (chung)").set_value("   ")
    _submit(at, "Thêm các phần việc đã chọn")  # đã chọn nhưng thiếu nội dung
    assert len(at.error) == 1
    assert ts.get_task(task_id)["members"] == []


def test_bulk_table_per_row_quantities_map_to_right_people(db_path):
    """Giả lập người dùng sửa bảng: tích chọn dòng 0 và 2, điền khối lượng khác nhau."""
    users = _make_users()
    task_id = ts.create_task(
        users["manager"], config.TYPE_TEAM, "Nhập bảng", None, _future(), lead_id=users["a"]["id"]
    )
    at = _open_task("nv_a", task_id)
    next(r for r in at.radio if r.label == "Cách nhập").set_value("Nhập bảng (nhiều người)").run()
    next(t for t in at.text_area if t.label == "Nội dung phần việc (chung)").set_value("Kiểm tra trạm")
    next(t for t in at.text_input if t.label == "Đơn vị (chung)").set_value("trạm")
    # Thứ tự bảng theo họ tên: 0 = Tên nv_a, 1 = Tên nv_b, 2 = Tên tpx
    at.session_state[f"bulk_table_{task_id}_0"] = {
        "edited_rows": {0: {"Chọn": True, "Khối lượng": 12}, 2: {"Chọn": True, "Khối lượng": 30}},
        "added_rows": [],
        "deleted_rows": [],
    }
    _submit(at, "Thêm các phần việc đã chọn")
    assert not at.error
    got = {m["full_name"]: m["quantity"] for m in ts.get_task(task_id)["members"]}
    assert got == {"Tên nv_a": 12.0, "Tên tpx": 30.0}  # nv_b không được chọn nên không có phần việc
