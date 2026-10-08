from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Iterable


def planned_actual_totals(conn: sqlite3.Connection, activity_id: int) -> tuple[float, float]:
    planned = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM activity_daily_plan WHERE activity_id=?",
        (activity_id,),
    ).fetchone()[0]
    actual = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM activity_daily_actual WHERE activity_id=?",
        (activity_id,),
    ).fetchone()[0]
    return float(planned or 0), float(actual or 0)


def activity_control(conn: sqlite3.Connection, activity_id: int) -> dict:
    row = conn.execute(
        "SELECT quantity, remaining_qty, daily_target, start_date, finish_date FROM activity WHERE id=?",
        (activity_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Activity not found.")

    planned, actual = planned_actual_totals(conn, activity_id)
    total = float(row["quantity"] or 0)
    baseline_qty = planned if planned > 0 else max(0.0, total - float(row["remaining_qty"] or 0))
    variance = actual - planned
    achievement = (actual / planned * 100.0) if planned else 0.0
    physical = (actual / total * 100.0) if total else 0.0

    return {
        "planned_qty": planned,
        "actual_qty": actual,
        "variance_qty": variance,
        "achievement_pct": achievement,
        "physical_progress_pct": physical,
        "baseline_qty": baseline_qty,
        "remaining_qty": max(0.0, total - actual),
    }


def project_finance(conn: sqlite3.Connection, project_id: int = 1) -> dict:
    row = conn.execute(
        "SELECT COALESCE(SUM(revenue),0) revenue, COALESCE(SUM(cost),0) cost "
        "FROM monthly_finance WHERE project_id=?",
        (project_id,),
    ).fetchone()
    revenue = float(row["revenue"] or 0)
    cost = float(row["cost"] or 0)
    return {
        "revenue": revenue,
        "cost": cost,
        "balance": revenue - cost,
    }


def detect_finance_discrepancy(
    summary_cost: float,
    detail_cost: float,
    tolerance: float = 1.0,
) -> dict | None:
    difference = float(detail_cost or 0) - float(summary_cost or 0)
    if abs(difference) <= tolerance:
        return None
    return {
        "title": "مغایرت هزینه خلاصه و جزئیات",
        "detail": (
            f"هزینه خلاصه {summary_cost:,.0f} ریال و هزینه جزئیات "
            f"{detail_cost:,.0f} ریال است؛ اختلاف {difference:,.0f} ریال."
        ),
        "severity": "HIGH" if abs(difference) > 1_000_000_000 else "MEDIUM",
    }


def scan_formula_errors(workbook) -> list[dict]:
    errors = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if isinstance(value, str) and "#REF!" in value:
                    errors.append(
                        {
                            "title": "خطای فرمول Excel",
                            "detail": f"{sheet.title}!{cell.coordinate}: {value}",
                            "severity": "HIGH",
                        }
                    )
    return errors


def forecast_finish(
    start: date | None,
    remaining_qty: float,
    actual_daily_rate: float,
    fallback_daily_rate: float = 0,
) -> date | None:
    rate = actual_daily_rate if actual_daily_rate > 0 else fallback_daily_rate
    if remaining_qty <= 0 or rate <= 0:
        return start
    days = max(1, math.ceil(remaining_qty / rate))
    return (start or date.today()) + timedelta(days=days - 1)


def aggregate_activity_control(conn: sqlite3.Connection, project_id: int = 1) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, zone, quantity, unit, status FROM activity "
        "WHERE project_id=? ORDER BY row_no, id",
        (project_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item.update(activity_control(conn, row["id"]))
        result.append(item)
    return result


def critical_activities(
    conn: sqlite3.Connection,
    project_id: int = 1,
    limit: int = 8,
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, row_no, title, zone, status, delay_days, quantity, remaining_qty, progress "
        "FROM activity WHERE project_id=?",
        (project_id,),
    ).fetchall()
    ranked = []

    for row in rows:
        item = dict(row)
        control = activity_control(conn, row["id"])
        has_daily_actuals = control["actual_qty"] > 0
        status = (row["status"] or "NORMAL").upper()
        delay = max(0, int(row["delay_days"] or 0))
        priority = {"CRITICAL": 3, "WARNING": 2}.get(status, 0)
        if delay:
            priority = max(priority, 2)
        if priority == 0:
            continue

        reasons = []
        if delay:
            reasons.append(f"تأخیر {delay} روز")
        if status in ("CRITICAL", "WARNING"):
            reasons.append(f"وضعیت {status}")
        item.update(
            {
                "priority": priority,
                "priority_label": "بحرانی" if priority == 3 else "نیازمند پیگیری",
                "reason": "، ".join(reasons),
                "remaining_qty": (
                    control["remaining_qty"]
                    if has_daily_actuals or row["remaining_qty"] is None
                    else float(row["remaining_qty"])
                ),
                "physical_progress_pct": (
                    control["physical_progress_pct"]
                    if has_daily_actuals
                    else float(row["progress"] or 0) * 100
                ),
            }
        )
        ranked.append(item)

    ranked.sort(
        key=lambda item: (
            -item["priority"],
            -max(0, int(item["delay_days"] or 0)),
            item["row_no"] if item["row_no"] is not None else item["id"],
        )
    )
    return ranked[: max(0, limit)]


def infer_activity_dependencies(conn: sqlite3.Connection, project_id: int = 1) -> list[dict]:
    rows = conn.execute(
        "SELECT id, row_no, position, zone, title, start_date, finish_date FROM activity "
        "WHERE project_id=? ORDER BY row_no, id",
        (project_id,),
    ).fetchall()

    created: list[dict] = []
    seen = set()

    for index, current in enumerate(rows):
        if not current["start_date"]:
            continue
        try:
            current_start = date.fromisoformat(str(current["start_date"])[:10])
        except ValueError:
            continue

        for previous in rows[:index]:
            if not previous["finish_date"]:
                continue
            try:
                previous_finish = date.fromisoformat(str(previous["finish_date"])[:10])
            except ValueError:
                continue

            same_group = (
                (current["position"] or "") == (previous["position"] or "")
                or (current["zone"] or "") == (previous["zone"] or "")
            )
            if not same_group:
                continue
            if current_start < previous_finish:
                continue

            key = (current["id"], previous["id"])
            if key in seen:
                continue
            seen.add(key)
            lag_days = (current_start - previous_finish).days
            conn.execute(
                """
                INSERT OR IGNORE INTO activity_dependency(
                    project_id, activity_id, predecessor_activity_id, relation_type, lag_days, notes
                ) VALUES (?, ?, ?, 'finish_to_start', ?, ?)
                """,
                (
                    project_id,
                    current["id"],
                    previous["id"],
                    lag_days,
                    f"استنتاج خودکار بر اساس ترتیب سطر و جبهه کاری: {current['title']} بعد از {previous['title']}",
                ),
            )
            created.append(
                {
                    "activity_id": current["id"],
                    "predecessor_activity_id": previous["id"],
                    "lag_days": lag_days,
                    "relation_type": "finish_to_start",
                }
            )

    return created
