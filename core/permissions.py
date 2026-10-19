"""Kiểm tra quyền ở tầng nghiệp vụ.

Mọi hàm nghiệp vụ gọi các hàm ở đây; UI chỉ dùng chúng để ẩn/hiện nút,
không bao giờ là lớp bảo vệ duy nhất. `task` là dict (hoặc sqlite3.Row)
có các khóa: status, task_type, proposer_id, lead_id.

Quyền theo từng công việc (người đề xuất, người chủ trì) được xác định từ
chính dữ liệu của công việc đó, không dựa vào vai trò toàn cục.
Quyền cập nhật tiến độ và đánh giá sẽ bổ sung ở Giai đoạn 3.
"""
from __future__ import annotations

from typing import Optional

import config


class PermissionDenied(Exception):
    """Người dùng không có quyền thực hiện thao tác."""


def _has_role(user: Optional[dict], *roles: str) -> bool:
    return bool(user) and bool(user.get("is_active", True)) and user.get("role") in roles


def require(condition: bool, message: str = "Bạn không có quyền thực hiện thao tác này.") -> None:
    if not condition:
        raise PermissionDenied(message)


# --- Vai trò toàn cục ---------------------------------------------------
def is_manager(user: Optional[dict]) -> bool:
    return _has_role(user, config.ROLE_MANAGER)


def _is_manager_or_staff(user: Optional[dict]) -> bool:
    return _has_role(user, config.ROLE_MANAGER, config.ROLE_STAFF)


def can_manage_accounts(user: Optional[dict]) -> bool:
    return _has_role(user, config.ROLE_ADMIN)


# --- Quan hệ với một công việc -----------------------------------------
def is_proposer(user: Optional[dict], task) -> bool:
    return bool(user) and task["proposer_id"] == user["id"]


def is_lead(user: Optional[dict], task) -> bool:
    return bool(user) and task["lead_id"] is not None and task["lead_id"] == user["id"]


# --- Công việc ----------------------------------------------------------
def can_create_task(user: Optional[dict], task_type: str) -> bool:
    """DIRECT: trưởng phòng; TEAM: trưởng phòng và nhân viên; PERSONAL: nhân viên."""
    if task_type == config.TYPE_DIRECT:
        return is_manager(user)
    if task_type == config.TYPE_TEAM:
        return _is_manager_or_staff(user)
    if task_type == config.TYPE_PERSONAL:
        return _has_role(user, config.ROLE_STAFF)
    return False


def can_edit_task(user: Optional[dict], task) -> bool:
    """Bản nháp / bị từ chối: người đề xuất hoặc trưởng phòng. Đang thực hiện: trưởng phòng."""
    status = task["status"]
    if status in (config.STATUS_DRAFT, config.STATUS_REJECTED):
        return _is_manager_or_staff(user) and (is_manager(user) or is_proposer(user, task))
    if status == config.STATUS_IN_PROGRESS:
        return is_manager(user)
    return False


def can_submit_task(user: Optional[dict], task) -> bool:
    """Gửi phê duyệt: TEAM/PERSONAL ở nháp/bị từ chối, do người đề xuất hoặc trưởng phòng."""
    return (
        task["task_type"] in (config.TYPE_TEAM, config.TYPE_PERSONAL)
        and task["status"] in (config.STATUS_DRAFT, config.STATUS_REJECTED)
        and _is_manager_or_staff(user)
        and (is_manager(user) or is_proposer(user, task))
    )


def can_decide_task(user: Optional[dict], task) -> bool:
    """Duyệt hoặc từ chối: chỉ trưởng phòng, khi công việc đang chờ phê duyệt."""
    return is_manager(user) and task["status"] == config.STATUS_PENDING_APPROVAL


def can_edit_allocation(user: Optional[dict], task) -> bool:
    """Thêm/sửa/xóa phần việc của thành viên.

    Bản nháp / bị từ chối: người đề xuất, người chủ trì hoặc trưởng phòng.
    Đang thực hiện: người chủ trì hoặc trưởng phòng.
    Công việc giao trực tiếp: chỉ trưởng phòng (người được giao là chủ trì nhưng không tự đổi phần việc của mình).
    """
    if not _is_manager_or_staff(user):
        return False
    status = task["status"]
    if task["task_type"] == config.TYPE_DIRECT:
        return is_manager(user) and status == config.STATUS_IN_PROGRESS
    if task["task_type"] == config.TYPE_PERSONAL:
        if status in (config.STATUS_DRAFT, config.STATUS_REJECTED):
            return is_manager(user) or is_proposer(user, task)
        return is_manager(user) and status == config.STATUS_IN_PROGRESS
    if status in (config.STATUS_DRAFT, config.STATUS_REJECTED):
        return is_manager(user) or is_proposer(user, task) or is_lead(user, task)
    if status == config.STATUS_IN_PROGRESS:
        return is_manager(user) or is_lead(user, task)
    return False


# --- Giai đoạn 3: tiến độ, nhận xét, báo cáo, đánh giá --------------------
def can_update_progress(user: Optional[dict], task, member) -> bool:
    """Việc nhóm: chủ trì cập nhật mọi thành viên; việc một người: người thực hiện tự cập nhật."""
    if not (_is_manager_or_staff(user) and task["status"] == config.STATUS_IN_PROGRESS):
        return False
    if task["task_type"] == config.TYPE_TEAM:
        return is_lead(user, task)
    return task["task_type"] in config.SINGLE_ASSIGNEE_TASK_TYPES and member["user_id"] == user["id"]


def can_comment_member(user: Optional[dict], task) -> bool:
    """Nhận xét phần việc: người chủ trì của công việc nhóm hoặc trưởng phòng, khi đang thực hiện hoặc chờ đánh giá.

    Công việc một người: chỉ trưởng phòng (người chủ trì chính là người thực hiện, không tự nhận xét mình).
    """
    if not (_is_manager_or_staff(user) and task["status"] in (config.STATUS_IN_PROGRESS, config.STATUS_REPORTED)):
        return False
    if task["task_type"] in config.SINGLE_ASSIGNEE_TASK_TYPES:
        return is_manager(user)
    return is_manager(user) or is_lead(user, task)


def can_report_completion(user: Optional[dict], task) -> bool:
    """Báo cáo hoàn thành công việc nhóm: chỉ người chủ trì. Việc một người tự báo cáo khi hoàn tất."""
    return (
        task["task_type"] == config.TYPE_TEAM
        and task["status"] == config.STATUS_IN_PROGRESS
        and _is_manager_or_staff(user)
        and is_lead(user, task)
    )


def can_evaluate(user: Optional[dict], task) -> bool:
    """Đánh giá đạt/không đạt: trưởng phòng, khi công việc đã báo cáo."""
    return is_manager(user) and task["status"] == config.STATUS_REPORTED


def can_reopen(user: Optional[dict], task) -> bool:
    """Mở lại công việc đánh giá 'Không đạt' để làm tiếp: trưởng phòng."""
    return is_manager(user) and task["status"] == config.STATUS_FAILED
