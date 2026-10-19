"""Sao lưu database SQLite an toàn (dùng API backup, đúng cả khi ứng dụng đang chạy).

Chạy từ thư mục gốc dự án:
    python -m scripts.backup_db
    python -m scripts.backup_db --keep 60 --dest /duong/dan/thu_muc_sao_luu

Mỗi lần chạy tạo một file app_YYYYMMDD_HHMMSS.db, kiểm tra tính toàn vẹn,
rồi chỉ giữ lại N bản mới nhất (mặc định 30) trong thư mục sao lưu.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import config

BACKUP_PREFIX = "app_"
BACKUP_SUFFIX = ".db"


def backup(dest_dir: Path, keep: int) -> Path:
    source_path = config.get_db_path()
    if not source_path.exists():
        raise FileNotFoundError(f"Không tìm thấy database: {source_path}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = dest_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"

    src = sqlite3.connect(str(source_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if result != "ok":
        dest_path.unlink(missing_ok=True)
        raise RuntimeError(f"Bản sao lưu không toàn vẹn ({result}); đã hủy bản sao lưu này.")

    if keep > 0:
        old = sorted(dest_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))[:-keep]
        for path in old:
            path.unlink()
    return dest_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sao lưu database.")
    parser.add_argument("--dest", type=Path, default=config.BACKUP_DIR, help="Thư mục lưu bản sao lưu")
    parser.add_argument("--keep", type=int, default=30, help="Số bản mới nhất cần giữ (0 = giữ tất cả)")
    args = parser.parse_args(argv)
    try:
        path = backup(args.dest, args.keep)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    print(f"Đã sao lưu: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
