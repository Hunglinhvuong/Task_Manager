import csv
import io
from datetime import date, timedelta

import pytest

import config
from core import auth, database, report_service as rs, task_service as ts
from core.timeutil import today_vn

PASSWORD = "MatKhau@123"


def _d(days):
    return (today_vn() + timedelta(days=days)).isoformat()


def _set_deadline(task_id, days):
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET deadline = ? WHERE id = ?", (_d(days), task_id))


@pytest.fixture
def scenario(db_path):
    """Dữ liệu có kết quả tính tay (xem các test bên dưới)."""

    def make(username, role):
        return auth.get_user(auth.bootstrap_user(username, f"Tên {username}", PASSWORD, role))

    u = {
        "admin": make("quantri", config.ROLE_ADMIN),
        "manager": make("tpx", config.ROLE_MANAGER),
        "a": make("nv_a", config.ROLE_STAFF),
        "b": make("nv_b", config.ROLE_STAFF),
        "c": make("nv_c", config.ROLE_STAFF),
    }
    manager = u["manager"]

    def member(task_id, key):
        return next(m for m in ts.get_task(task_id)["members"] if m["user_id"] == u[key]["id"])

    def direct(title, key):
        """Giao trực tiếp cho một người; người đó là chủ trì."""
        return ts.create_task(
            manager, config.TYPE_DIRECT, title, None, _d(2),
            lead_id=u[key]["id"], members=[{"content": "Làm"}],
        )

    def team_running(title, lead, parts):
        task_id = ts.create_task(u[lead], config.TYPE_TEAM, title, None, _d(3), lead_id=u[lead]["id"])
        for key, qty in parts:
            ts.add_member(u[lead], task_id, u[key]["id"], f"Phần {key}", qty, "trạm")
        ts.submit_task(u[lead], task_id)
        ts.approve_task(manager, task_id)
        return task_id

    # T1: việc nhóm đang thực hiện, chủ trì A; B xong 10/10 (100%), C làm 2.5/5 (50%), hạn +3
    t1 = team_running("T1", "a", [("b", 10), ("c", 5)])
    ts.update_progress(u["b"], member(t1, "b")["id"], 10, "B xong")
    ts.update_progress(u["c"], member(t1, "c")["id"], 2.5, None)

    # T2: giao A; xong; đánh giá Đạt
    t2 = direct("T2", "a")
    ts.update_progress(u["a"], member(t2, "a")["id"], 100, "a xong")
    ts.evaluate_task(manager, t2, config.EVAL_PASS, "Tốt")

    # T3: giao C; xong; đánh giá Không đạt
    t3 = direct("T3", "c")
    ts.update_progress(u["c"], member(t3, "c")["id"], 100, "C xong")
    ts.evaluate_task(manager, t3, config.EVAL_FAIL, "Chưa đạt")

    # T4: bản nháp, quá hạn 5 ngày
    t4 = ts.create_task(u["b"], config.TYPE_TEAM, "T4", None, _d(5), lead_id=u["b"]["id"])
    _set_deadline(t4, -5)

    # T5: chờ phê duyệt, hạn +10
    t5 = ts.create_task(u["c"], config.TYPE_TEAM, "T5", None, _d(10), lead_id=u["c"]["id"])
    ts.add_member(u["c"], t5, u["a"]["id"], "Phần a", 1, "x")
    ts.submit_task(u["c"], t5)
    _set_deadline(t5, 10)

    # T6: giao A, đang thực hiện, tiến độ 0, quá hạn 1 ngày
    t6 = direct("T6", "a")
    _set_deadline(t6, -1)

    # T7: giao B; xong; đánh giá Đạt
    t7 = direct("T7", "b")
    ts.update_progress(u["b"], member(t7, "b")["id"], 100, "b xong")
    ts.evaluate_task(manager, t7, config.EVAL_PASS, None)

    u["tasks"] = dict(t1=t1, t2=t2, t3=t3, t4=t4, t5=t5, t6=t6, t7=t7)
    return u


# --- Tổng quan -----------------------------------------------------------------------
def test_overview_status_counts_and_headlines(scenario):
    data = rs.overview()
    counts = data["status_counts"]
    assert counts == {
        config.STATUS_DRAFT: 1,
        config.STATUS_PENDING_APPROVAL: 1,
        config.STATUS_REJECTED: 0,
        config.STATUS_IN_PROGRESS: 2,
        config.STATUS_REPORTED: 0,
        config.STATUS_PASSED: 2,
        config.STATUS_FAILED: 1,
    }
    assert data["total"] == 7
    assert (data["in_progress"], data["pending_approvals"], data["pending_evaluations"]) == (2, 1, 0)
    assert (data["passed"], data["failed"]) == (2, 1)
    # Phần việc của việc đang thực hiện: T1 (b 100, c 50) và T6 (a 0) -> TB 50
    assert data["avg_progress_in_progress"] == 50


def test_overview_overdue_and_due_soon(scenario):
    t = scenario["tasks"]
    data = rs.overview()
    # Quá hạn: T4 (bản nháp, hạn -5) và T6 (đang thực hiện, hạn -1); sắp xếp theo hạn
    assert [x["id"] for x in data["overdue"]] == [t["t4"], t["t6"]]
    assert data["overdue_count"] == 2
    # Sắp đến hạn 7 ngày: chỉ việc ĐANG THỰC HIỆN có hạn từ hôm nay: T1 (+3)
    assert [x["id"] for x in data["due_soon"]] == [t["t1"]]
    assert [x["id"] for x in rs.overview(due_soon_days=0)["due_soon"]] == []


def test_overdue_boundary_today_is_not_overdue(scenario):
    t = scenario["tasks"]
    _set_deadline(t["t6"], 0)  # hạn là hôm nay: chưa quá hạn
    assert [x["id"] for x in rs.overview()["overdue"]] == [t["t4"]]
    assert [x["id"] for x in rs.overview()["due_soon"]] == [t["t6"], t["t1"]]


def test_overview_workload_per_person(scenario):
    rows = {r["full_name"]: r for r in rs.overview()["workload"]}
    assert "Tên quantri" not in rows  # ADMIN không được giao việc
    a, b, c, tp = rows["Tên nv_a"], rows["Tên nv_b"], rows["Tên nv_c"], rows["Tên tpx"]
    assert (a["active_parts"], a["done_parts"], a["avg_progress"], a["overdue_parts"], a["leading"]) == (1, 0, 0, 1, 2)  # chủ trì T1 và T6 (giao trực tiếp)
    assert (b["active_parts"], b["done_parts"], b["avg_progress"], b["overdue_parts"], b["leading"]) == (1, 1, 100, 0, 0)
    assert (c["active_parts"], c["done_parts"], c["avg_progress"], c["overdue_parts"], c["leading"]) == (1, 0, 50, 0, 0)
    assert (tp["active_parts"], tp["avg_progress"], tp["leading"]) == (0, None, 0)


def test_overview_excludes_inactive_people_and_handles_empty_database(db_path):
    data = rs.overview()
    assert data["total"] == 0 and data["workload"] == [] and data["avg_progress_in_progress"] is None
    assert all(v == 0 for v in data["status_counts"].values())


def test_overview_hides_locked_accounts_from_workload(scenario):
    auth.set_active(scenario["admin"], scenario["c"]["id"], False)
    assert "Tên nv_c" not in {r["full_name"] for r in rs.overview()["workload"]}


# --- Báo cáo công việc ----------------------------------------------------------------
def test_task_report_filters(scenario):
    t = scenario["tasks"]
    ids = lambda rows: [r["id"] for r in rows]  # noqa: E731
    assert ids(rs.task_report()) == [t["t4"], t["t6"], t["t2"], t["t3"], t["t7"], t["t1"], t["t5"]]  # theo hạn
    assert ids(rs.task_report(statuses=[config.STATUS_PASSED, config.STATUS_FAILED])) == [t["t2"], t["t3"], t["t7"]]
    assert ids(rs.task_report(task_type=config.TYPE_TEAM)) == [t["t4"], t["t1"], t["t5"]]
    today = today_vn()
    assert ids(rs.task_report(date_from=today, date_to=today + timedelta(days=5))) == [t["t2"], t["t3"], t["t7"], t["t1"]]
    assert ids(rs.task_report(date_to=today - timedelta(days=2))) == [t["t4"]]  # chỉ có cận trên
    assert ids(rs.task_report(date_from=today + timedelta(days=10))) == [t["t5"]]  # chỉ có cận dưới
    assert ids(rs.task_report(date_from=today + timedelta(days=11))) == []
    # Lọc theo người: A đề xuất/chủ trì/được giao: T1 (chủ trì), T2, T5 (có phần), T6
    assert set(ids(rs.task_report(user_id=scenario["a"]["id"]))) == {t["t1"], t["t2"], t["t5"], t["t6"]}


def test_task_report_row_content(scenario):
    t = scenario["tasks"]
    rows = {r["id"]: r for r in rs.task_report()}
    t1 = rows[t["t1"]]
    assert (t1["title"], t1["task_type"], t1["status"]) == ("T1", config.TYPE_TEAM, config.STATUS_IN_PROGRESS)
    assert (t1["lead_name"], t1["proposer_name"], t1["member_count"], t1["avg_progress"]) == ("Tên nv_a", "Tên nv_a", 2, 75)
    assert t1["overdue"] is False
    assert rows[t["t6"]]["overdue"] is True and rows[t["t4"]]["overdue"] is True
    assert rows[t["t2"]]["eval_result"] == config.EVAL_PASS
    assert rows[t["t3"]]["eval_comment"] == "Chưa đạt"
    assert rows[t["t2"]]["completed_at"]
    assert rows[t["t3"]]["overdue"] is False  # đã báo cáo thì không còn tính quá hạn


def test_outcome_summary(scenario):
    summary = rs.outcome_summary(rs.task_report())
    assert {k: v for k, v in summary.items() if k != "pass_rate"} == {"total": 7, "passed": 2, "failed": 1, "overdue": 2}
    assert summary["pass_rate"] == pytest.approx(2 / 3)
    empty = rs.outcome_summary([])
    assert (empty["total"], empty["pass_rate"]) == (0, None)
    only_open = rs.outcome_summary(rs.task_report(statuses=[config.STATUS_DRAFT]))
    assert only_open["pass_rate"] is None  # chưa có việc nào được đánh giá


# --- Báo cáo theo người ----------------------------------------------------------------
def test_member_report_all_time(scenario):
    rows = {r["full_name"]: r for r in rs.member_report()}
    a, b, c = rows["Tên nv_a"], rows["Tên nv_b"], rows["Tên nv_c"]
    # A: T2 (100, Đạt) + T6 (0, quá hạn, đang thực hiện); chủ trì T1, T2 (giao trực tiếp) và T6
    assert (a["parts"], a["completed"], a["avg_progress"], a["passed"], a["failed"], a["overdue_parts"]) == (2, 1, 50, 1, 0, 1)
    assert (a["led_total"], a["led_passed"], a["led_failed"]) == (3, 1, 0)
    # B: T1 (100) + T7 (100, Đạt); chủ trì T7
    assert (b["led_total"], b["led_passed"], b["led_failed"]) == (1, 1, 0)
    assert (b["parts"], b["completed"], b["avg_progress"], b["passed"], b["failed"], b["overdue_parts"]) == (2, 2, 100, 1, 0, 0)
    # C: T1 (50) + T3 (100, Không đạt); chủ trì T3
    assert (c["led_total"], c["led_passed"], c["led_failed"]) == (1, 0, 1)
    assert (c["parts"], c["completed"], c["avg_progress"], c["passed"], c["failed"], c["overdue_parts"]) == (2, 1, 75, 0, 1, 0)
    # Bản nháp và chờ duyệt (T4, T5) không tính vào thống kê thực hiện
    assert rows["Tên tpx"]["parts"] == 0 and rows["Tên tpx"]["avg_progress"] is None


def test_member_report_respects_date_range(scenario):
    today = today_vn()
    rows = {r["full_name"]: r for r in rs.member_report(date_from=today + timedelta(days=2), date_to=today + timedelta(days=2))}
    # Chỉ T2, T3 và T7 có hạn +2 (đều giao trực tiếp)
    assert rows["Tên nv_a"]["parts"] == 1 and rows["Tên nv_a"]["passed"] == 1
    assert rows["Tên nv_b"]["parts"] == 1
    assert rows["Tên nv_c"]["parts"] == 1 and rows["Tên nv_c"]["failed"] == 1
    assert rows["Tên nv_a"]["led_total"] == 1  # chỉ T2; T1 (hạn +3) nằm ngoài khoảng


def test_member_report_led_outcomes(scenario):
    t = scenario["tasks"]
    u = scenario
    ts.update_progress(u["c"], next(m for m in ts.get_task(t["t1"])["members"] if m["user_id"] == u["c"]["id"])["id"], 5, "C xong")
    ts.report_completion(u["a"], t["t1"], "Xong")
    ts.evaluate_task(u["manager"], t["t1"], config.EVAL_PASS, None)
    a = next(r for r in rs.member_report() if r["full_name"] == "Tên nv_a")
    assert (a["led_total"], a["led_passed"], a["led_failed"]) == (3, 2, 0)  # T1, T2 đạt; T6 đang thực hiện


# --- Kỳ báo cáo ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,today,expected",
    [
        ("Tất cả thời gian", date(2026, 10, 18), (None, None)),
        ("Tùy chọn", date(2026, 10, 18), (None, None)),
        ("Tháng này", date(2026, 10, 18), (date(2026, 10, 1), date(2026, 10, 31))),
        ("Tháng này", date(2026, 2, 10), (date(2026, 2, 1), date(2026, 2, 28))),
        ("Tháng này", date(2028, 2, 29), (date(2028, 2, 1), date(2028, 2, 29))),  # năm nhuận
        ("Tháng này", date(2026, 12, 31), (date(2026, 12, 1), date(2026, 12, 31))),
        ("Tháng trước", date(2026, 3, 1), (date(2026, 2, 1), date(2026, 2, 28))),
        ("Tháng trước", date(2026, 1, 15), (date(2025, 12, 1), date(2025, 12, 31))),
        ("Quý này", date(2026, 2, 5), (date(2026, 1, 1), date(2026, 3, 31))),
        ("Quý này", date(2026, 6, 30), (date(2026, 4, 1), date(2026, 6, 30))),
        ("Quý này", date(2026, 11, 30), (date(2026, 10, 1), date(2026, 12, 31))),
        ("Năm nay", date(2026, 10, 18), (date(2026, 1, 1), date(2026, 12, 31))),
    ],
)
def test_period_range(name, today, expected):
    assert rs.period_range(name, today) == expected


# --- Xuất CSV ----------------------------------------------------------------------------------
def _read_csv(data: bytes):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_csv_has_bom_headers_and_vietnamese_text():
    data = rs.to_csv([("title", "Tên công việc"), ("n", "Số người")], [{"title": "Kiểm tra trạm Đà Nẵng", "n": 3}])
    assert data.startswith(b"\xef\xbb\xbf")  # BOM để Excel đọc đúng tiếng Việt
    assert _read_csv(data) == [["Tên công việc", "Số người"], ["Kiểm tra trạm Đà Nẵng", "3"]]


def test_csv_quotes_commas_newlines_and_handles_none():
    rows = [{"a": 'Có "dấu ngoặc", dấu phẩy', "b": "dòng 1\ndòng 2", "c": None}]
    assert _read_csv(rs.to_csv([("a", "A"), ("b", "B"), ("c", "C")], rows))[1] == [
        'Có "dấu ngoặc", dấu phẩy',
        "dòng 1\ndòng 2",
        "",
    ]


@pytest.mark.parametrize("payload", ["=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\t=1+1"])
def test_csv_neutralizes_formula_injection(payload):
    out = _read_csv(rs.to_csv([("t", "Tên")], [{"t": payload}]))
    assert out[1][0] == "'" + payload  # Excel coi là văn bản, không chạy công thức


def test_csv_does_not_alter_numbers_or_safe_text():
    out = _read_csv(rs.to_csv([("n", "N"), ("t", "T")], [{"n": -5, "t": "bình thường"}]))
    assert out[1] == ["-5", "bình thường"]  # số âm là số, không phải văn bản bị chèn công thức


def test_csv_for_real_report(scenario):
    rows = rs.task_report()
    data = rs.to_csv([("id", "Mã"), ("title", "Tên công việc"), ("status", "Trạng thái")], rows)
    parsed = _read_csv(data)
    assert len(parsed) == 1 + len(rows)
    assert parsed[1][1] == "T4"
