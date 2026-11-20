"""Critical-path analysis using the project's explicit holiday calendar.

All durations and relationship lags use one day unit. A date is excluded
from scheduled time only when it is registered in calendar_holiday.
"""
from __future__ import annotations

from app.services.work_calendar import (
    day_index,
    finish_date,
    normalize_workday,
    parse_date,
    shift_days,
)


def shift_working_days(start, offset, working_weekdays=None, holidays=()):
    """Backward-compatible name; only explicitly registered holidays are skipped."""
    return shift_days(start, offset, holidays)


def working_finish(start, duration_days, working_weekdays=None, holidays=()):
    """Backward-compatible name for inclusive finish-date calculation."""
    return finish_date(start, duration_days, holidays)


def analyze_critical_path(
    activities,
    dependencies=(),
    project_start=None,
    working_weekdays=None,
    holidays=(),
):
    """Run CPM without persisting changes.

    Duration and lag are scheduled days. Explicit project holidays are skipped;
    weekdays are otherwise treated identically. Activity finish dates are
    inclusive. FS+0 starts on the next scheduled day.
    """
    holidays = tuple(holidays)
    items = {}
    for raw in activities:
        activity_id = raw.get("id")
        if activity_id is None:
            raise ValueError("each activity must have an id")
        duration = int(raw.get("duration_days") or 1)
        if duration < 1:
            raise ValueError("duration_days must be at least 1")
        items[activity_id] = {
            "id": activity_id,
            "title": raw.get("title") or str(activity_id),
            "duration_days": duration,
            "start_date": parse_date(raw.get("start_date")),
        }
    if not items:
        return {
            "activities": [], "cycle": [], "project_duration_days": 0,
            "project_duration_workdays": 0, "critical_activity_ids": [],
        }

    incoming = {key: [] for key in items}
    outgoing = {key: [] for key in items}
    indegree = {key: 0 for key in items}
    relations = {
        "fs": "fs", "finish_to_start": "fs",
        "ss": "ss", "start_to_start": "ss",
        "ff": "ff", "finish_to_finish": "ff",
        "sf": "sf", "start_to_finish": "sf",
    }
    for raw in dependencies:
        successor, predecessor = raw.get("activity_id"), raw.get("predecessor_activity_id")
        if successor not in items or predecessor not in items:
            continue
        key = str(raw.get("relation_type") or "finish_to_start").lower().replace("-", "_")
        if key not in relations:
            raise ValueError("unsupported dependency relation: " + key)
        relation = relations[key]
        lag = int(raw.get("lag_days") or 0)
        pred_duration = items[predecessor]["duration_days"]
        succ_duration = items[successor]["duration_days"]
        if relation == "fs":
            offset = pred_duration + lag
        elif relation == "ss":
            offset = lag
        elif relation == "ff":
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
            "activities": [], "cycle": cycle,
            "cycle_titles": [items[key]["title"] for key in cycle],
            "project_duration_days": None, "project_duration_workdays": None,
        }

    anchor = parse_date(project_start)
    if anchor is None:
        dated = [item["start_date"] for item in items.values() if item["start_date"]]
        anchor = min(dated) if dated else None
    if anchor is not None:
        anchor = normalize_workday(anchor, holidays)

    earliest_start = {
        key: (
            day_index(anchor, item["start_date"], holidays)
            if anchor is not None and item["start_date"] is not None else 0
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
            latest_start[current] = min(
                latest_start[current],
                latest_start[edge["successor"]] - edge["offset"],
            )

    results = []
    for key in order:
        duration = items[key]["duration_days"]
        es, ef, ls = earliest_start[key], earliest_finish[key], latest_start[key]
        total_float = ls - es
        result = {
            **items[key],
            "earliest_start_day": es,
            "earliest_finish_day": ef,
            "latest_start_day": ls,
            "latest_finish_day": ls + duration - 1,
            "total_float_days": total_float,
            # Compatibility aliases retained for existing consumers.
            "earliest_start_workday": es,
            "earliest_finish_workday": ef,
            "latest_start_workday": ls,
            "latest_finish_workday": ls + duration - 1,
            "total_float_workdays": total_float,
            "critical": total_float <= 0,
        }
        if anchor is not None:
            result["earliest_start"] = shift_days(anchor, es, holidays)
            result["earliest_finish"] = finish_date(result["earliest_start"], duration, holidays)
            result["latest_start"] = shift_days(anchor, ls, holidays)
            result["latest_finish"] = finish_date(result["latest_start"], duration, holidays)
        else:
            result["earliest_start"] = result["earliest_finish"] = None
            result["latest_start"] = result["latest_finish"] = None
        results.append(result)

    duration = project_finish_index + 1
    return {
        "activities": results,
        "cycle": [],
        "project_duration_days": duration,
        "project_duration_workdays": duration,
        "critical_activity_ids": [item["id"] for item in results if item["critical"]],
    }
