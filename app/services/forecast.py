"""Schedule propagation, progress-linked finance estimates, and resource shortage checks.

All schedule calculations use calendar days. Finance projections only allocate
amounts explicitly linked to an activity; unallocated Excel totals are reported
separately rather than assigned to activities by guesswork.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict, deque
from datetime import date, timedelta

from app.services.control import activity_control, forecast_finish
from app.services.dates import normalize_date
from app.services.activity_costs import activity_cost_budgets


def _as_date(value) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        try:
            return date.fromisoformat(normalize_date(value))
        except (ValueError, TypeError):
            return None


def _activity_duration(row) -> int:
    duration = int(row["duration_days"] or 0)
    if duration > 0:
        return duration
    start = _as_date(row["start_date"])
    finish = _as_date(row["finish_date"])
    if start and finish and finish >= start:
        return (finish - start).days + 1
    quantity = max(0.0, float(row["quantity"] or 0))
    target = max(0.0, float(row["daily_target"] or 0))
    return max(1, math.ceil(quantity / target)) if target else 1


def _observed_finish(conn, row, remaining: float, as_of: date) -> date | None:
    days = conn.execute(
        """
        SELECT actual_date, quantity FROM activity_daily_actual
        WHERE activity_id=? AND source='MANUAL'
        ORDER BY actual_date, id
        """,
        (row["id"],),
    ).fetchall()
    if not days:
        return None
    last_day = _as_date(days[-1]["actual_date"])
    if last_day is None:
        return None
    if remaining <= 0:
        return last_day
    quantities = [max(0.0, float(item["quantity"] or 0)) for item in days]
    positive = [quantity for quantity in quantities if quantity > 0]
    rate = sum(positive) / len(positive) if positive else 0.0
    if rate <= 0:
        rate = max(0.0, float(row["daily_target"] or 0))
    return forecast_finish(last_day + timedelta(days=1), remaining, rate) if rate > 0 else None


def calculate_schedule(
    conn: sqlite3.Connection,
    project_id: int = 1,
    as_of: date | None = None,
    persist: bool = True,
) -> dict:
    """Calculate forecast dates and propagate predecessor delays.

    Supported relationships: finish_to_start (FS), start_to_start (SS),
    finish_to_finish (FF), and start_to_finish (SF). lag_days is a calendar-day
    offset from the predecessor date. A cycle is returned as a validation error;
    no partial schedule is persisted in that case.
    """
    as_of = as_of or date.today()
    rows = conn.execute(
        """
        SELECT id, row_no, title, start_date, finish_date, forecast_finish,
               duration_days, daily_target, quantity, remaining_qty, delay_days
        FROM activity WHERE project_id=? ORDER BY row_no, id
        """,
        (project_id,),
    ).fetchall()
    activities = {row["id"]: dict(row) for row in rows}
    if not activities:
        return {"activities": [], "cycle": [], "updated": 0}

    dependencies = conn.execute(
        """
        SELECT activity_id, predecessor_activity_id, relation_type, lag_days
        FROM activity_dependency
        WHERE project_id=? AND activity_id IN ({}) AND predecessor_activity_id IN ({})
        """.format(
            ",".join("?" for _ in activities),
            ",".join("?" for _ in activities),
        ),
        (project_id, *activities.keys(), *activities.keys()),
    ).fetchall()
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    indegree = {activity_id: 0 for activity_id in activities}
    for dep in dependencies:
        successor = dep["activity_id"]
        predecessor = dep["predecessor_activity_id"]
        if successor == predecessor:
            incoming[successor].append(dict(dep))
            indegree[successor] += 1
            continue
        incoming[successor].append(dict(dep))
        outgoing[predecessor].append(successor)
        indegree[successor] += 1

    queue = deque(
        sorted(
            (activity_id for activity_id, degree in indegree.items() if degree == 0),
            key=lambda item: (activities[item]["row_no"] or item, item),
        )
    )
    order = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for successor in outgoing[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if len(order) != len(activities):
        cycle_ids = [activity_id for activity_id, degree in indegree.items() if degree > 0]
        return {
            "activities": [],
            "cycle": cycle_ids,
            "cycle_titles": [activities[item]["title"] or str(item) for item in cycle_ids],
            "updated": 0,
        }

    calculated = {}
    for activity_id in order:
        row = activities[activity_id]
        baseline_start = _as_date(row["start_date"])
        baseline_finish = _as_date(row["finish_date"])
        duration = _activity_duration(row)
        control = activity_control(conn, activity_id)
        remaining = control["remaining_qty"]
        observed_finish = _observed_finish(conn, row, remaining, as_of)
        initial_finish = observed_finish or baseline_finish
        if initial_finish is None and baseline_start is not None:
            initial_finish = baseline_start + timedelta(days=duration - 1)
        initial_start = baseline_start
        if initial_start is None and initial_finish is not None:
            initial_start = initial_finish - timedelta(days=duration - 1)

        required_start = initial_start
        required_finish = initial_finish
        for dep in incoming[activity_id]:
            predecessor_id = dep["predecessor_activity_id"]
            predecessor = calculated.get(predecessor_id)
            if predecessor is None:
                continue
            relation = (dep["relation_type"] or "finish_to_start").lower().replace("-", "_")
            lag = int(dep["lag_days"] or 0)
            pred_start = predecessor["forecast_start"]
            pred_finish = predecessor["forecast_finish"]
            candidate_start = None
            if relation in ("finish_to_start", "fs") and pred_finish:
                candidate_start = pred_finish + timedelta(days=lag)
            elif relation in ("start_to_start", "ss") and pred_start:
                candidate_start = pred_start + timedelta(days=lag)
            elif relation in ("finish_to_finish", "ff") and pred_finish:
                candidate_finish = pred_finish + timedelta(days=lag)
                candidate_start = candidate_finish - timedelta(days=duration - 1)
            elif relation in ("start_to_finish", "sf") and pred_start:
                candidate_finish = pred_start + timedelta(days=lag)
                candidate_start = candidate_finish - timedelta(days=duration - 1)
            if candidate_start is not None and (required_start is None or candidate_start > required_start):
                required_start = candidate_start

        # A dependency delay moves the finish by the same amount unless a later
        # observed productivity forecast already governs the activity.
        if initial_start and required_start and required_start > initial_start:
            shift = required_start - initial_start
            required_finish = max(
                required_finish or (required_start + timedelta(days=duration - 1)),
                required_start + timedelta(days=duration - 1),
            )
        elif required_start and required_finish is None:
            required_finish = required_start + timedelta(days=duration - 1)
        if required_finish and required_start and required_finish < required_start:
            required_finish = required_start + timedelta(days=duration - 1)

        baseline_finish_for_delay = baseline_finish
        delay_days = (
            max(0, (required_finish - baseline_finish_for_delay).days)
            if required_finish and baseline_finish_for_delay
            else max(0, int(row["delay_days"] or 0))
        )
        calculated[activity_id] = {
            "id": activity_id,
            "row_no": row["row_no"],
            "title": row["title"],
            "baseline_start": baseline_start,
            "baseline_finish": baseline_finish,
            "forecast_start": required_start,
            "forecast_finish": required_finish,
            "delay_days": delay_days,
            "remaining_qty": remaining,
        }

    if persist:
        for item in calculated.values():
            conn.execute(
                "UPDATE activity SET forecast_finish=?, delay_days=? WHERE id=?",
                (
                    item["forecast_finish"].isoformat() if item["forecast_finish"] else None,
                    item["delay_days"],
                    item["id"],
                ),
            )
    return {"activities": list(calculated.values()), "cycle": [], "updated": len(calculated)}


def forecast_finance(conn: sqlite3.Connection, project_id: int = 1) -> dict:
    """Progress-weight financial amounts only where a row is linked to an activity.

    amount is treated as an activity budget/allocation, not actual expenditure.
    Imported summary rows without activity_id remain unallocated.
    """
    result = {}
    unit_cost_budgets = activity_cost_budgets(conn, project_id)
    for table, name in (("revenue_entry", "revenue"), ("cost_entry", "cost")):
        rows = conn.execute(
            f"""
            SELECT id, activity_id, amount, source FROM {table}
            WHERE project_id=? AND source IN ('EXCEL','MANUAL','EXCEL_ACTIVITY')
            """,
            (project_id,),
        ).fetchall()
        has_activity_revenue = name == "revenue" and any(
            row["source"] == "EXCEL_ACTIVITY" and row["activity_id"] is not None
            for row in rows
        )
        has_activity_cost_budget = name == "cost" and bool(unit_cost_budgets)
        linked_budget = 0.0
        earned_to_date = 0.0
        unallocated = 0.0
        summary_reference = 0.0
        linked_count = 0
        for entry in rows:
            amount = float(entry["amount"] or 0)
            activity_id = entry["activity_id"]
            if name == "cost" and activity_id in unit_cost_budgets:
                # The per-unit cost build-up is the authoritative budget for this activity.
                # Do not add an older flat cost_entry allocation on top of it.
                continue
            if activity_id is None:
                if (
                    (has_activity_revenue and name == "revenue" and entry["source"] == "EXCEL")
                    or (has_activity_cost_budget and name == "cost" and entry["source"] == "EXCEL")
                ):
                    summary_reference += amount
                else:
                    unallocated += amount
                continue
            activity = conn.execute(
                "SELECT quantity FROM activity WHERE id=? AND project_id=?",
                (activity_id, project_id),
            ).fetchone()
            if activity is None:
                unallocated += amount
                continue
            total_qty = max(0.0, float(activity["quantity"] or 0))
            control = activity_control(conn, activity_id)
            progress_fraction = min(1.0, max(0.0, control["physical_progress_pct"] / 100.0))
            linked_budget += amount
            earned_to_date += amount * progress_fraction
            linked_count += 1

        if name == "cost":
            for activity_id, amount in unit_cost_budgets.items():
                activity = conn.execute(
                    "SELECT quantity FROM activity WHERE id=? AND project_id=?",
                    (activity_id, project_id),
                ).fetchone()
                if activity is None:
                    continue
                control = activity_control(conn, activity_id)
                progress_fraction = min(1.0, max(0.0, control["physical_progress_pct"] / 100.0))
                linked_budget += amount
                earned_to_date += amount * progress_fraction
                linked_count += 1

        result[name] = {
            "linked_budget": linked_budget,
            "earned_to_date": earned_to_date,
            "forecast_at_completion": linked_budget,
            "unallocated_amount": unallocated,
            "summary_reference_amount": summary_reference,
            "summary_reconciliation_difference": summary_reference - linked_budget if summary_reference else None,
            "linked_entries": linked_count,
            "coverage_pct": (
                linked_budget / (linked_budget + unallocated) * 100.0
                if linked_budget + unallocated > 0 else 0.0
            ),
        }
    result["limitations"] = (
        "مبالغ فعالیت‌محور فقط برای ردیف‌های دارای activity_id محاسبه شده‌اند؛ "
        "مبالغ بدون تخصیص فعالیت جدا گزارش می‌شوند و داده واقعی پرداخت/هزینه ثبت‌شده محسوب نمی‌شوند."
    )
    return result


def resource_shortages(conn: sqlite3.Connection, project_id: int = 1) -> list[dict]:
    """Return per-period shortages; unknown supply is not silently treated as zero."""
    rows = conn.execute(
        """
        SELECT r.id AS resource_id, r.category, r.title, r.unit, r.available AS resource_available,
               rp.period, rp.required_qty, rp.available_qty, rp.opening_stock,
               rp.purchase_qty, rp.unit_price
        FROM resource r
        JOIN resource_period rp ON rp.resource_id=r.id
        WHERE r.project_id=? AND rp.source IN ('EXCEL','MANUAL')
        ORDER BY r.category, r.title, rp.period
        """,
        (project_id,),
    ).fetchall()
    results = []
    for row in rows:
        required = max(0.0, float(row["required_qty"] or 0))
        if row["available_qty"] is not None:
            available = max(0.0, float(row["available_qty"]))
            availability_basis = "available_qty"
        elif row["opening_stock"] is not None and row["purchase_qty"] is not None:
            available = max(0.0, float(row["opening_stock"]) + float(row["purchase_qty"]))
            availability_basis = "opening_stock+purchase_qty"
        elif row["resource_available"] is not None:
            available = max(0.0, float(row["resource_available"]))
            availability_basis = "resource.available"
        else:
            available = None
            availability_basis = "unknown"
        shortage = max(0.0, required - available) if available is not None else None
        results.append(
            {
                "resource_id": row["resource_id"],
                "category": row["category"],
                "title": row["title"],
                "unit": row["unit"],
                "period": row["period"],
                "required_qty": required,
                "available_qty": available,
                "shortage_qty": shortage,
                "availability_basis": availability_basis,
                "unit_price": row["unit_price"],
                "shortage_cost": shortage * float(row["unit_price"] or 0) if shortage is not None else None,
            }
        )
    return results
