"""Giao diện Giai đoạn 4: Tổng quan và Báo cáo."""
from datetime import timedelta

import pandas as pd
import pyarrow as pa
from streamlit.testing.v1 import AppTest

import config
from core import auth, database, report_service, task_service as ts
from core.timeutil import today_vn
from ui import reports

APP_FILE = str(config.BASE_DIR / "app.py")
PASSWORD = "MatKhau@123"


def _day(offset):
    return (today_vn() + timedelta(days=offset)).isoformat()


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


def _seed(users):
    m = users["manager"]
    # Đang thực hiện, đã quá hạn
    t1 = ts.create_task(m, config.TYPE_DIRECT, "Việc quá hạn", None, _day(3),
                        lead_id=users["a"]["id"], members=[{"content": "P1", "quantity": 10, "unit": "trạm"}])
    with database.transaction() as conn:  # tạo xong mới lùi hạn (không cho tạo hạn quá khứ)
        conn.execute("UPDATE tasks SET deadline = ? WHERE id = ?", (_day(-3), t1))
    # Đang thực hiện, sắp đến hạn
    ts.create_task(m, config.TYPE_DIRECT, "Việc sắp đến hạn", None, _day(2),
                   lead_id=users["b"]["id"], members=[{"content": "P2"}])
    # Chờ phê duyệt
    t3 = ts.create_task(users["a"], config.TYPE_TEAM, "Việc chờ duyệt", None, _day(10), lead_id=users["a"]["id"])
    ts.add_member(users["a"], t3, users["b"]["id"], "P3")
    ts.submit_task(users["a"], t3)
    return t1


def test_overview_all_roles_render(db_path):
    users = _make_users()
    _seed(users)
    for username in ("tpx", "nv_a", "quantri"):
        at = _login(username, "Tổng quan")
        assert at.main.header[0].value == "Tổng quan"
        assert len(at.metric) == 5


def test_overview_numbers_and_tabs(db_path):
    users = _make_users()
    _seed(users)
    at = _login("tpx", "Tổng quan")
    values = {m.label: m.value for m in at.metric}
    assert values["Đang thực hiện"] == "2"
    assert values["⚠ Quá hạn"] == "1"
    assert values["Chờ phê duyệt"] == "1"
    assert values["Chờ đánh giá"] == "0"
    labels = [t.label for t in at.tabs]
    assert any(l.startswith("⚠ Quá hạn (1)") for l in labels)
    assert any(l.startswith("Sắp đến hạn 7 ngày (1)") for l in labels)
    assert any("chờ bạn phê duyệt" in i.value for i in at.info)


def test_overview_empty_database(db_path):
    _make_users()
    at = _login("tpx", "Tổng quan")
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Đang thực hiện"] == "0"


def test_overview_staff_does_not_see_manager_notes(db_path):
    users = _make_users()
    _seed(users)
    at = _login("nv_b", "Tổng quan")
    assert not any("chờ bạn phê duyệt" in i.value for i in at.info)


def test_reports_default_and_filters(db_path):
    users = _make_users()
    _seed(users)
    at = _login("tpx", "Báo cáo")
    assert at.main.header[0].value == "Báo cáo"
    assert not at.exception
    total = {m.label: m.value for m in at.metric}["Tổng số công việc"]
    assert int(total) >= 0

    # Đổi loại sang Nhóm: chỉ còn việc nhóm
    team_label = config.TASK_TYPE_LABELS[config.TYPE_TEAM]
    type_box = next(s for s in at.selectbox if s.label == "Loại")
    type_box.set_value(config.TYPE_TEAM).run()
    assert not at.exception
    assert team_label  # nhãn tồn tại
    assert {m.label: m.value for m in at.metric}["Tổng số công việc"] in ("0", "1")


def test_report_progress_columns_are_arrow_compatible_with_missing_values(db_path):
    users = _make_users()
    ts.create_task(users["a"], config.TYPE_TEAM, "Chưa phân công", None, _day(5), lead_id=users["a"]["id"])
    ts.create_task(
        users["manager"],
        config.TYPE_DIRECT,
        "Đã phân công",
        None,
        _day(5),
        lead_id=users["b"]["id"],
        members=[{"content": "Phần việc"}],
    )

    task_rows = reports._prepare_task_rows(report_service.task_report())
    task_data = pd.DataFrame([{title: row[key] for key, title in reports._TASK_COLUMNS} for row in task_rows])
    pa.Table.from_pandas(task_data, preserve_index=False)
    assert {row["progress_text"] for row in task_rows} == {None, 0}

    member_rows = reports._prepare_member_rows(report_service.member_report())
    member_data = pd.DataFrame([{title: row[key] for key, title in reports._MEMBER_COLUMNS} for row in member_rows])
    pa.Table.from_pandas(member_data, preserve_index=False)
    assert any(row["progress_text"] is None for row in member_rows)
    assert any(row["progress_text"] == 0 for row in member_rows)


def test_reports_custom_range_invalid_order(db_path):
    _make_users()
    at = _login("tpx", "Báo cáo")
    period = next(s for s in at.selectbox if s.label.startswith("Kỳ báo cáo"))
    period.set_value("Tùy chọn").run()
    assert not at.exception
    assert len(at.date_input) == 1


def test_reports_visible_to_staff_and_admin(db_path):
    users = _make_users()
    _seed(users)
    for username in ("nv_a", "quantri"):
        at = _login(username, "Báo cáo")
        assert at.main.header[0].value == "Báo cáo"
        assert not at.exception


# --- Việc giao trực tiếp: một người, người đó là chủ trì ----------------------------------
def _direct_for(users, key):
    return ts.create_task(
        users["manager"], config.TYPE_DIRECT, "Việc giao một người", None, _day(5),
        lead_id=users[key]["id"], members=[{"content": "Làm việc được giao", "quantity": 4, "unit": "bản"}],
    )


def test_direct_task_detail_has_no_add_or_remove_part(db_path):
    users = _make_users()
    task_id = _direct_for(users, "a")
    at = _login("tpx", "Danh sách công việc")
    at.selectbox(key="task_picker").set_value(task_id).run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert "Thêm phần việc" not in labels
    assert "Sửa hoặc xóa phần việc" in labels  # trưởng phòng vẫn sửa được nội dung/khối lượng
    assert not any(b.label == "Xóa phần việc" for b in at.button)
    assert any("chỉ có một người thực hiện" in c.value for c in at.caption)
    assert any("Người thực hiện (chủ trì)" in m.value and "Tên nv_a" in m.value for m in at.markdown)


def test_direct_assignee_sees_part_not_in_leading_section(db_path):
    users = _make_users()
    _direct_for(users, "a")
    at = _login("nv_a", "Công việc của tôi")
    assert not at.exception
    headings = [s.value for s in at.main.subheader]
    assert "Công việc tôi chủ trì (0)" in headings  # việc giao trực tiếp nằm ở phần việc của tôi
    assert "Phần việc của tôi (1)" in headings
