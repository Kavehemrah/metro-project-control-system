"""Independent working-calendar and critical-path analysis.

This module is read-only: it does not change the existing calendar-day
forecast engine or write calculated dates back to SQLite. Durations and
dependency lags are measured in working days.
"""
from __future__ import annotations

from datetime import date, timedelta


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _is_working_day(day, weekdays, holidays):
    return day.weekday() in weekdays and day not in holidays


def shift_working_days(start, offset, working_weekdays=(0, 1, 2, 3, 5, 6), holidays=()):
    """Shift a date by working days, skipping non-working weekdays and holidays."""
    current = _parse_date(start)
    if current is None:
        raise ValueError("start must be an ISO date or datetime.date")
    weekdays = set(working_weekdays)
    holiday_dates = {_parse_date(item) for item in holidays}
    holiday_dates.discard(None)
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        raise ValueError("working_weekdays must contain weekday numbers from 0 to 6")
    direction = -1 if offset < 0 else 1
    remaining = abs(int(offset))
    while not _is_working_day(current, weekdays, holiday_dates):
        current += timedelta(days=direction)
    while remaining:
        current += timedelta(days=direction)
        if _is_working_day(current, weekdays, holiday_dates):
            remaining -= 1
    return current


def working_finish(start, duration_days, working_weekdays=(0, 1, 2, 3, 5, 6), holidays=()):
    """Return the inclusive finish date for a duration in working days."""
    duration = int(duration_days)
    if duration < 1:
        raise ValueError("duration_days must be at least 1")
    return shift_working_days(start, duration - 1, working_weekdays, holidays)




def _working_day_index(anchor, target, working_weekdays, holidays):
    """Return the signed working-day offset of target from anchor."""
    anchor = shift_working_days(anchor, 0, working_weekdays, holidays)
    target = shift_working_days(target, 0, working_weekdays, holidays)
    weekdays = set(working_weekdays)
    holiday_dates = {_parse_date(item) for item in holidays}
    holiday_dates.discard(None)
    if target >= anchor:
        current = anchor
        offset = 0
        while current < target:
            current += timedelta(days=1)
            if _is_working_day(current, weekdays, holiday_dates):
                offset += 1
        return offset
    current = anchor
    offset = 0
    while current > target:
        current -= timedelta(days=1)
        if _is_working_day(current, weekdays, holiday_dates):
            offset -= 1
    return offset


def analyze_critical_path(
    activities,
    dependencies,
    project_start=None,
    working_weekdays=(0, 1, 2, 3, 5, 6),
    holidays=(),
):
    """Run a CPM pass without persisting changes.

    Activities require id and may include title, duration_days and start_date.
    Dependencies use activity_id for successor, predecessor_activity_id,
    relation_type and optional lag_days. FS, SS, FF and SF are supported.
    The default workweek is Saturday through Thursday.
    """
    items = {}
    for raw in activities:
        activity_id = raw.get("id")
        if activity_id is None:
            raise ValueError("each activity must have an id")
        duration = max(1, int(raw.get("duration_days") or 1))
        items[activity_id] = {
            "id": activity_id,
            "title": raw.get("title") or str(activity_id),
            "duration_days": duration,
            "start_date": _parse_date(raw.get("start_date")),
        }
    if not items:
        return {"activities": [], "cycle": [], "project_duration_workdays": 0}

    incoming = {key: [] for key in items}
    outgoing = {key: [] for key in items}
    indegree = {key: 0 for key in items}
    valid_relations = {
        "fs": "finish_to_start", "finish_to_start": "finish_to_start",
        "ss": "start_to_start", "start_to_start": "start_to_start",
        "ff": "finish_to_finish", "finish_to_finish": "finish_to_finish",
        "sf": "start_to_finish", "start_to_finish": "start_to_finish",
    }
    for raw in dependencies:
        successor = raw.get("activity_id")
        predecessor = raw.get("predecessor_activity_id")
        if successor not in items or predecessor not in items:
            continue
        relation_key = str(raw.get("relation_type") or "finish_to_start").lower().replace("-", "_")
        if relation_key not in valid_relations:
            raise ValueError("unsupported dependency relation: " + relation_key)
        relation = valid_relations[relation_key]
        lag = int(raw.get("lag_days") or 0)
        pred_duration = items[predecessor]["duration_days"]
        succ_duration = items[successor]["duration_days"]
        if relation == "finish_to_start":
            offset = pred_duration + lag
        elif relation == "start_to_start":
            offset = lag
        elif relation == "finish_to_finish":
            offset = pred_duration + lag - succ_duration
        else:
            offset = lag - succ_duration
        edge = {"predecessor": predecessor, "successor": successor, "offset": offset}
        incoming[successor].append(edge)
        outgoing[predecessor].append(edge)
        indegree[successor] += 1

    ready = sorted((key for key, degree in indegree.items() if degree == 0), key=str)
    order = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for edge in outgoing[current]:
            successor = edge["successor"]
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort(key=str)
    if len(order) != len(items):
        cycle = [key for key, degree in indegree.items() if degree > 0]
        return {
            "activities": [],
            "cycle": cycle,
            "cycle_titles": [items[key]["title"] for key in cycle],
            "project_duration_workdays": None,
        }

    anchor = _parse_date(project_start)
    if anchor is None:
        dated = [item["start_date"] for item in items.values() if item["start_date"]]
        anchor = min(dated) if dated else None
    holidays = tuple(holidays)
    if anchor is not None:
        anchor = shift_working_days(anchor, 0, working_weekdays, holidays)
    earliest_start = {
        key: (
            _working_day_index(anchor, item["start_date"], working_weekdays, holidays)
            if anchor is not None and item["start_date"] is not None
            else 0
        )
        for key, item in items.items()
    }
    for current in order:
        for edge in incoming[current]:
            earliest_start[current] = max(
                earliest_start[current],
                earliest_start[edge["predecessor"]] + edge["offset"],
            )
    earliest_finish = {
        key: earliest_start[key] + items[key]["duration_days"] - 1 for key in items
    }
    project_finish_index = max(earliest_finish.values())
    latest_start = {
        key: project_finish_index - items[key]["duration_days"] + 1 for key in items
    }
    for current in reversed(order):
        for edge in outgoing[current]:
            successor = edge["successor"]
            latest_start[current] = min(
                latest_start[current], latest_start[successor] - edge["offset"]
            )

    results = []
    for key in order:
        duration = items[key]["duration_days"]
        es, ef = earliest_start[key], earliest_finish[key]
        ls = latest_start[key]
        result = {
            **items[key],
            "earliest_start_workday": es,
            "earliest_finish_workday": ef,
            "latest_start_workday": ls,
            "latest_finish_workday": ls + duration - 1,
            "total_float_workdays": max(0, ls - es),
            "critical": ls == es,
        }
        if anchor is not None:
            result["earliest_start"] = shift_working_days(anchor, es, working_weekdays, holidays)
            result["earliest_finish"] = working_finish(result["earliest_start"], duration, working_weekdays, holidays)
            result["latest_start"] = shift_working_days(anchor, ls, working_weekdays, holidays)
            result["latest_finish"] = working_finish(result["latest_start"], duration, working_weekdays, holidays)
        else:
            result["earliest_start"] = result["earliest_finish"] = None
            result["latest_start"] = result["latest_finish"] = None
        results.append(result)
    return {
        "activities": results,
        "cycle": [],
        "project_duration_workdays": project_finish_index + 1,
        "critical_activity_ids": [item["id"] for item in results if item["critical"]],
    }
