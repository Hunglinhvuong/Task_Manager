"""Thời gian: lưu UTC (ISO 8601, hậu tố Z), hiển thị theo giờ Việt Nam (UTC+7)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

VN_TZ = timezone(timedelta(hours=7))  # Việt Nam không có giờ mùa hè
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def today_vn() -> date:
    """Ngày hôm nay theo giờ Việt Nam."""
    return datetime.now(VN_TZ).date()


def now_iso() -> str:
    """Thời điểm hiện tại (UTC) dạng chuỗi ISO, cùng định dạng với default của database."""
    return datetime.now(timezone.utc).strftime(_ISO_FORMAT)


def to_local_str(iso_value: Optional[str], fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Đổi chuỗi UTC lưu trong DB sang giờ Việt Nam để hiển thị."""
    if not iso_value:
        return ""
    try:
        dt = datetime.strptime(iso_value, _ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return iso_value
    return dt.astimezone(VN_TZ).strftime(fmt)
