"""Cấu hình và hằng số dùng chung.

Vai trò, loại công việc và trạng thái là giá trị cố định trong code
(không có bảng danh mục riêng trong database).
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_TITLE = "Quản lý giao việc nội bộ"

DEFAULT_DB_PATH = BASE_DIR / "data" / "app.db"
BACKUP_DIR = BASE_DIR / "data" / "backups"


def get_db_path() -> Path:
    """Đường dẫn database. Đọc biến môi trường mỗi lần gọi để dễ kiểm thử."""
    return Path(os.environ.get("TASKS_DB_PATH", str(DEFAULT_DB_PATH)))


# --- Vai trò -----------------------------------------------------------
ROLE_ADMIN = "ADMIN"
ROLE_MANAGER = "MANAGER"
ROLE_STAFF = "STAFF"
ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_STAFF)
ROLE_LABELS = {
    ROLE_ADMIN: "Quản trị viên",
    ROLE_MANAGER: "Trưởng phòng",
    ROLE_STAFF: "Nhân viên",
}

# --- Loại công việc ----------------------------------------------------
TYPE_DIRECT = "DIRECT"
TYPE_TEAM = "TEAM"
TYPE_PERSONAL = "PERSONAL"
TASK_TYPES = (TYPE_DIRECT, TYPE_TEAM, TYPE_PERSONAL)
SINGLE_ASSIGNEE_TASK_TYPES = (TYPE_DIRECT, TYPE_PERSONAL)
TASK_TYPE_LABELS = {
    TYPE_DIRECT: "Giao trực tiếp",
    TYPE_TEAM: "Công việc nhóm",
    TYPE_PERSONAL: "Công việc cá nhân",
}

# --- Trạng thái công việc ----------------------------------------------
# DRAFT -> PENDING_APPROVAL -> (REJECTED | IN_PROGRESS) -> REPORTED -> (PASSED | FAILED)
STATUS_DRAFT = "DRAFT"
STATUS_PENDING_APPROVAL = "PENDING_APPROVAL"
STATUS_REJECTED = "REJECTED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_REPORTED = "REPORTED"
STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUSES = (
    STATUS_DRAFT,
    STATUS_PENDING_APPROVAL,
    STATUS_REJECTED,
    STATUS_IN_PROGRESS,
    STATUS_REPORTED,
    STATUS_PASSED,
    STATUS_FAILED,
)
STATUS_LABELS = {
    STATUS_DRAFT: "Bản nháp",
    STATUS_PENDING_APPROVAL: "Chờ phê duyệt",
    STATUS_REJECTED: "Bị từ chối",
    STATUS_IN_PROGRESS: "Đang thực hiện",
    STATUS_REPORTED: "Đã báo cáo, chờ đánh giá",
    STATUS_PASSED: "Đạt",
    STATUS_FAILED: "Không đạt",
}

# --- Kết quả đánh giá --------------------------------------------------
EVAL_PASS = "PASS"
EVAL_FAIL = "FAIL"
EVAL_RESULTS = (EVAL_PASS, EVAL_FAIL)
EVAL_LABELS = {EVAL_PASS: "Đạt", EVAL_FAIL: "Không đạt"}

# Trạng thái còn "mở" (chưa báo cáo xong): dùng để xác định quá hạn.
OPEN_STATUSES = (
    STATUS_DRAFT,
    STATUS_PENDING_APPROVAL,
    STATUS_REJECTED,
    STATUS_IN_PROGRESS,
)

# Chỉ ADMIN không được giao việc / làm thành viên / chủ trì.
ASSIGNABLE_ROLES = (ROLE_MANAGER, ROLE_STAFF)

# Giới hạn độ dài dữ liệu nhập
MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 4000
MAX_PART_LENGTH = 1000
MAX_UNIT_LENGTH = 30

# --- Hành động ghi trong task_logs --------------------------------------
LOG_TASK_CREATED = "TASK_CREATED"
LOG_TASK_UPDATED = "TASK_UPDATED"
LOG_SUBMITTED = "SUBMITTED"
LOG_APPROVED = "APPROVED"
LOG_REJECTED = "REJECTED"
LOG_MEMBER_ADDED = "MEMBER_ADDED"
LOG_MEMBER_UPDATED = "MEMBER_UPDATED"
LOG_MEMBER_REMOVED = "MEMBER_REMOVED"
LOG_MEMBER_PROGRESS = "MEMBER_PROGRESS"
LOG_MEMBER_COMMENT = "MEMBER_COMMENT"
LOG_REPORTED = "REPORTED"
LOG_EVALUATED = "EVALUATED"
LOG_REOPENED = "REOPENED"
LOG_LABELS = {
    LOG_MEMBER_PROGRESS: "Cập nhật tiến độ",
    LOG_MEMBER_COMMENT: "Nhận xét phần việc",
    LOG_REPORTED: "Báo cáo hoàn thành",
    LOG_EVALUATED: "Đánh giá kết quả",
    LOG_REOPENED: "Mở lại công việc",
    LOG_TASK_CREATED: "Tạo công việc",
    LOG_TASK_UPDATED: "Sửa công việc",
    LOG_SUBMITTED: "Gửi phê duyệt",
    LOG_APPROVED: "Phê duyệt",
    LOG_REJECTED: "Từ chối",
    LOG_MEMBER_ADDED: "Thêm phần việc",
    LOG_MEMBER_UPDATED: "Sửa phần việc",
    LOG_MEMBER_REMOVED: "Xóa phần việc",
}

# --- Tài khoản ---------------------------------------------------------
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
USERNAME_PATTERN = r"^[a-z0-9._-]{3,32}$"
