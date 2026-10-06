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
