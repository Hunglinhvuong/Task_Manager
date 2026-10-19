"""Xác thực và quản lý tài khoản.

- Mật khẩu được băm bằng scrypt (thư viện chuẩn của Python) với salt ngẫu
  nhiên cho từng tài khoản; không bao giờ lưu mật khẩu dạng rõ.
- Các hàm quản lý tài khoản nhận `actor` (người thực hiện) và tự kiểm tra
  quyền ADMIN ở tầng nghiệp vụ.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import sqlite3
from typing import List, Optional

import config
from core import database, permissions
from core.timeutil import now_iso

# --- Băm mật khẩu ------------------------------------------------------
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024

_dummy_hash_cache: Optional[str] = None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _dummy_hash() -> str:
    """Băm giả để thời gian phản hồi không lộ tên đăng nhập có tồn tại hay không."""
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password("khong-ton-tai")
    return _dummy_hash_cache


# --- Kiểm tra dữ liệu đầu vào -----------------------------------------
def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < config.MIN_PASSWORD_LENGTH:
        raise ValueError(f"Mật khẩu phải có ít nhất {config.MIN_PASSWORD_LENGTH} ký tự.")
    if len(password) > config.MAX_PASSWORD_LENGTH:
        raise ValueError(f"Mật khẩu tối đa {config.MAX_PASSWORD_LENGTH} ký tự.")


def normalize_username(username: str) -> str:
    value = (username or "").strip().lower()
    if not re.fullmatch(config.USERNAME_PATTERN, value):
        raise ValueError(
            "Tên đăng nhập gồm 3-32 ký tự: chữ thường không dấu, số, dấu chấm, gạch dưới hoặc gạch ngang."
        )
    return value


def _validate_role(role: str) -> None:
    if role not in config.ROLES:
        raise ValueError("Vai trò không hợp lệ.")


# --- Truy vấn người dùng (không bao giờ trả password_hash) -------------
_PUBLIC_COLUMNS = "id, username, full_name, role, is_active, created_at"


def _to_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }


def get_user(user_id: int) -> Optional[dict]:
    with database.connect() as conn:
        row = conn.execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _to_user(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    with database.connect() as conn:
        row = conn.execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM users WHERE username = ?",
            ((username or "").strip().lower(),),
        ).fetchone()
    return _to_user(row) if row else None


def list_users(active_only: bool = False) -> List[dict]:
    sql = f"SELECT {_PUBLIC_COLUMNS} FROM users"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY full_name COLLATE NOCASE, id"
    with database.connect() as conn:
        return [_to_user(r) for r in conn.execute(sql).fetchall()]


# --- Đăng nhập ---------------------------------------------------------
def authenticate(username: str, password: str) -> Optional[dict]:
    """Trả về thông tin người dùng nếu đúng tài khoản/mật khẩu và đang hoạt động."""
    uname = (username or "").strip().lower()
    with database.connect() as conn:
        row = conn.execute(
            f"SELECT {_PUBLIC_COLUMNS}, password_hash FROM users WHERE username = ?",
            (uname,),
        ).fetchone()
    stored = row["password_hash"] if row else _dummy_hash()
    password_ok = verify_password(password or "", stored)
    if row is None or not password_ok or not row["is_active"]:
        return None
    return _to_user(row)


# --- Tạo / sửa tài khoản ----------------------------------------------
def _insert_user(
    conn: sqlite3.Connection, username: str, full_name: str, password: str, role: str
) -> int:
    uname = normalize_username(username)
    name = (full_name or "").strip()
    if not name:
        raise ValueError("Họ tên không được để trống.")
    _validate_role(role)
    validate_password(password)
    exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (uname,)).fetchone()
    if exists:
        raise ValueError(f"Tên đăng nhập '{uname}' đã tồn tại.")
    cur = conn.execute(
        "INSERT INTO users (username, full_name, password_hash, role, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 1, ?, ?)",
        (uname, name, hash_password(password), role, now_iso(), now_iso()),
    )
    return int(cur.lastrowid)


def bootstrap_user(username: str, full_name: str, password: str, role: str) -> int:
    """Tạo tài khoản không cần người thực hiện. CHỈ dùng cho script (tạo admin đầu tiên, dữ liệu mẫu)."""
    with database.transaction() as conn:
        return _insert_user(conn, username, full_name, password, role)


def create_user(actor: dict, username: str, full_name: str, password: str, role: str) -> int:
    permissions.require(permissions.can_manage_accounts(actor), "Chỉ ADMIN mới được tạo tài khoản.")
    with database.transaction() as conn:
        return _insert_user(conn, username, full_name, password, role)


def _fetch_target(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, role, is_active FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy tài khoản.")
    return row


def _active_admin_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1", (config.ROLE_ADMIN,)
        ).fetchone()[0]
    )


def reset_password(actor: dict, user_id: int, new_password: str) -> None:
    permissions.require(permissions.can_manage_accounts(actor), "Chỉ ADMIN mới được đặt lại mật khẩu.")
    validate_password(new_password)
    with database.transaction() as conn:
        _fetch_target(conn, user_id)
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), now_iso(), user_id),
        )


def change_password(actor: dict, current_password: str, new_password: str) -> None:
    """Cho phép người dùng đang hoạt động tự đổi mật khẩu sau khi xác minh mật khẩu hiện tại."""
    validate_password(new_password)
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT password_hash, is_active FROM users WHERE id = ?", (actor["id"],)
        ).fetchone()
        if row is None or not row["is_active"]:
            raise permissions.PermissionDenied("Tài khoản không còn hoạt động.")
        if not verify_password(current_password or "", row["password_hash"]):
            raise ValueError("Mật khẩu hiện tại không đúng.")
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), now_iso(), actor["id"]),
        )


def set_active(actor: dict, user_id: int, active: bool) -> None:
    permissions.require(permissions.can_manage_accounts(actor), "Chỉ ADMIN mới được khóa/mở tài khoản.")
    with database.transaction() as conn:
        target = _fetch_target(conn, user_id)
        if not active:
            if target["id"] == actor["id"]:
                raise ValueError("Không thể tự khóa tài khoản của chính mình.")
            if (
                target["role"] == config.ROLE_ADMIN
                and target["is_active"]
                and _active_admin_count(conn) <= 1
            ):
                raise ValueError("Không thể khóa quản trị viên đang hoạt động cuối cùng.")
        conn.execute(
            "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, now_iso(), user_id),
        )


def set_role(actor: dict, user_id: int, role: str) -> None:
    permissions.require(permissions.can_manage_accounts(actor), "Chỉ ADMIN mới được đổi vai trò.")
    _validate_role(role)
    with database.transaction() as conn:
        target = _fetch_target(conn, user_id)
        if (
            target["role"] == config.ROLE_ADMIN
            and role != config.ROLE_ADMIN
            and target["is_active"]
            and _active_admin_count(conn) <= 1
        ):
            raise ValueError("Không thể bỏ quyền quản trị viên đang hoạt động cuối cùng.")
        conn.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
            (role, now_iso(), user_id),
        )
