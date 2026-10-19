"""Tạo dữ liệu mẫu để kiểm thử: 1 trưởng phòng + 12 nhân viên.

Chạy từ thư mục gốc dự án:  python -m scripts.seed_demo
Tuỳ chọn:                   python -m scripts.seed_demo --password "MatKhauMau@1"

CHỈ DÙNG ĐỂ THỬ NGHIỆM: tất cả tài khoản mẫu dùng chung một mật khẩu.
Không chạy trên database thật đang vận hành. Chạy lại không tạo trùng
(tài khoản đã có sẽ được bỏ qua).
"""
from __future__ import annotations

import argparse
import sys

import config
from core import auth, database

DEFAULT_PASSWORD = "Demo@12345"

MANAGER = ("truongphong", "Trần Quang Minh")

STAFF = [
    ("nv01", "Nguyễn Văn An"),
    ("nv02", "Trần Thị Bình"),
    ("nv03", "Lê Hoàng Cường"),
    ("nv04", "Phạm Thị Dung"),
    ("nv05", "Hoàng Văn Em"),
    ("nv06", "Vũ Thị Phương"),
    ("nv07", "Đặng Quốc Gia"),
    ("nv08", "Bùi Thị Hà"),
    ("nv09", "Đỗ Minh Hiếu"),
    ("nv10", "Ngô Thị Lan"),
    ("nv11", "Dương Văn Long"),
    ("nv12", "Lý Thị Mai"),
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tạo dữ liệu mẫu (1 trưởng phòng + 12 nhân viên).")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Mật khẩu chung cho tài khoản mẫu")
    args = parser.parse_args(argv)

    database.init_db()
    accounts = [(*MANAGER, config.ROLE_MANAGER)] + [(u, n, config.ROLE_STAFF) for u, n in STAFF]
    created, skipped = 0, 0
    try:
        for username, full_name, role in accounts:
            if auth.get_user_by_username(username):
                skipped += 1
                continue
            auth.bootstrap_user(username, full_name, args.password, role)
            created += 1
    except ValueError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1

    print(f"Đã tạo {created} tài khoản mẫu, bỏ qua {skipped} tài khoản đã có.")
    print(f"Tài khoản: {MANAGER[0]} (trưởng phòng), nv01 ... nv12 (nhân viên). Mật khẩu: {args.password}")
    print("CẢNH BÁO: dữ liệu mẫu chỉ để thử nghiệm, không dùng trên hệ thống thật.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
