"""Tạo database và schema (an toàn khi chạy lại).

Chạy từ thư mục gốc dự án:  python -m scripts.init_db
"""
from __future__ import annotations

import config
from core import database


def main() -> int:
    path = database.init_db()
    print(f"Đã khởi tạo database: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
