"""Date normalization helpers for user-entered Jalali and Gregorian dates."""

from __future__ import annotations

import re
from datetime import date, datetime

import jdatetime


_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_date(value: object) -> str:
    """Return a canonical Gregorian YYYY-MM-DD string.

    Years below 1700 are interpreted as Jalali (Solar Hijri), so entries such
    as 1405/07/15 are safely converted before being compared with Excel dates.
    Gregorian dates in ISO format (for example 2026-10-07) are preserved.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        raise ValueError("تاریخ وارد نشده است.")

    text = str(value).translate(_DIGIT_TRANSLATION).strip()
    if not text:
        raise ValueError("تاریخ وارد نشده است.")

    parts = re.split(r"[./-]", text)
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("قالب تاریخ معتبر نیست؛ نمونه: 1405/07/15 یا 2026-10-07.")

    year, month, day = (int(part) for part in parts)
    try:
        if year < 1700:
            return jdatetime.date(year, month, day).togregorian().isoformat()
        return date(year, month, day).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"تاریخ «{text}» معتبر نیست.") from exc
