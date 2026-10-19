"""Tạo tài khoản quản trị (ADMIN) đầu tiên.

Chạy từ thư mục gốc dự án:
    python -m scripts.create_admin
    python -m scripts.create_admin --username quantri --full-name "Quản trị hệ thống"

Mật khẩu được hỏi trực tiếp (không hiện trên màn hình). Có thể đặt biến môi
trường ADMIN_PASSWORD để chạy không tương tác, nhưng cách này có thể để lại
mật khẩu trong lịch sử lệnh nên chỉ dùng khi thật cần.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

import config
from core import auth, database


def _read_password() -> str:
    env_password = os.environ.get("ADMIN_PASSWORD")
    if env_password:
        return env_password
    first = getpass.getpass("Mật khẩu: ")
    second = getpass.getpass("Nhập lại mật khẩu: ")
    if first != second:
        raise ValueError("Hai lần nhập mật khẩu không khớp.")
    return first


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tạo tài khoản ADMIN đầu tiên.")
    parser.add_argument("--username", default="admin", help="Tên đăng nhập (mặc định: admin)")
    parser.add_argument("--full-name", default="Quản trị hệ thống", help="Họ tên hiển thị")
    args = parser.parse_args(argv)

    database.init_db()
    try:
        password = _read_password()
        user_id = auth.bootstrap_user(args.username, args.full_name, password, config.ROLE_ADMIN)
    except ValueError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    print(f"Đã tạo tài khoản ADMIN '{args.username.strip().lower()}' (id={user_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
