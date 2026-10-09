"""Canonical date conversion between Jalali UI dates and Gregorian DB dates."""

from __future__ import annotations

import re
from datetime import date, datetime

import jdatetime


_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_DATE_TOKEN = re.compile(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})")


def normalize_date(value: object) -> str:
    """Return canonical Gregorian YYYY-MM-DD, accepting Jalali input and timestamps.

    DB dates remain ISO Gregorian so date arithmetic and sorting are dependable.
    Values such as '2026-10-09 00:00:00', '00:00:00 2026-10-09', and
    '1405/07/17 00:00:00' are parsed using their date component only.
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

    match = _DATE_TOKEN.search(text)
    if match is None:
        raise ValueError("قالب تاریخ معتبر نیست؛ نمونه: 1405/07/17 یا 2026-10-09.")

    year, month, day = (int(part) for part in match.groups())
    try:
        if year < 1700:
            return jdatetime.date(year, month, day).togregorian().isoformat()
        return date(year, month, day).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"تاریخ «{text}» معتبر نیست.") from exc


def format_date(value: object, separator: str = "/") -> str:
    """Format ISO/Gregorian DB dates or Jalali inputs as Jalali YYYY/MM/DD."""
    if value is None or not str(value).strip():
        return ""
    try:
        gregorian = date.fromisoformat(normalize_date(value))
        jalali = jdatetime.date.fromgregorian(date=gregorian)
        return f"{jalali.year:04d}{separator}{jalali.month:02d}{separator}{jalali.day:02d}"
    except (ValueError, TypeError, OverflowError):
        return str(value).strip()
