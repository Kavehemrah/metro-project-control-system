"""Project calendar utilities.

Every date is a calendar date. Only holidays explicitly registered for the
project are excluded from scheduled working time; weekdays are not implicitly
treated as non-working days.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


def parse_date(value) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def normalize_workday(value, holidays: Iterable = (), direction: int = 1) -> date:
    day = parse_date(value)
    if day is None:
        raise ValueError("date must be an ISO date or datetime.date")
    excluded = {parse_date(item) for item in holidays}
    excluded.discard(None)
    step = -1 if direction < 0 else 1
    while day in excluded:
        day += timedelta(days=step)
    return day


def shift_days(value, offset: int, holidays: Iterable = ()) -> date:
    """Shift by scheduled days, skipping explicitly registered holidays."""
    day = normalize_workday(value, holidays, 1 if offset >= 0 else -1)
    remaining = abs(int(offset))
    step = 1 if offset >= 0 else -1
    excluded = {parse_date(item) for item in holidays}
    excluded.discard(None)
    while remaining:
        day += timedelta(days=step)
        if day not in excluded:
            remaining -= 1
    return day


def finish_date(start, duration_days: int, holidays: Iterable = ()) -> date:
    duration = int(duration_days)
    if duration < 1:
        raise ValueError("duration_days must be at least 1")
    return shift_days(start, duration - 1, holidays)


def day_index(anchor, target, holidays: Iterable = ()) -> int:
    """Return signed scheduled-day offset between two dates."""
    anchor_day = normalize_workday(anchor, holidays, 1)
    target_day = normalize_workday(target, holidays, 1)
    if target_day >= anchor_day:
        current, result, step = anchor_day, 0, 1
    else:
        current, result, step = anchor_day, 0, -1
    excluded = {parse_date(item) for item in holidays}
    excluded.discard(None)
    while current != target_day:
        current += timedelta(days=step)
        if current not in excluded:
            result += step
    return result


def project_holidays(conn, project_id: int) -> tuple[date, ...]:
    rows = conn.execute(
        "SELECT holiday_date FROM calendar_holiday WHERE project_id=? ORDER BY holiday_date",
        (project_id,),
    ).fetchall()
    return tuple(day for row in rows if (day := parse_date(row["holiday_date"])) is not None)
