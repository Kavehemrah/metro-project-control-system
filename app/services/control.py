from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Iterable


def planned_actual_totals(
    conn: sqlite3.Connection, activity_id: int
) -> tuple[float, float, float]:
    """Return period plan, period actual, and cumulative actual quantities.

    Excel period actuals exclude opening quantities. Manual actuals only count
    toward period achievement when their dates fall inside the available plan
    window; all manual actuals still contribute to cumulative progress.
    """
    planned = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM activity_daily_plan WHERE activity_id=?",
        (activity_id,),
    ).fetchone()[0]

    manual_actual_total = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0)
        FROM activity_daily_actual
        WHERE activity_id=? AND source='MANUAL'
        """,
        (activity_id,),
    ).fetchone()[0]
    manual_actual_total = float(manual_actual_total or 0)

    plan_window = conn.execute(
        """
        SELECT MIN(plan_date) AS first_date, MAX(plan_date) AS last_date
        FROM activity_daily_plan
        WHERE activity_id=?
        """,
        (activity_id,),
    ).fetchone()
    if plan_window["first_date"] and plan_window["last_date"]:
        manual_period_actual = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM activity_daily_actual
            WHERE activity_id=? AND source='MANUAL'
              AND actual_date BETWEEN ? AND ?
            """,
            (activity_id, plan_window["first_date"], plan_window["last_date"]),
        ).fetchone()[0]
    else:
        manual_period_actual = manual_actual_total

    excel_period_actual = conn.execute(
        """
        SELECT COALESCE(SUM(actual_qty), 0)
        FROM activity_period
        WHERE activity_id=? AND source='EXCEL' AND COALESCE(planned_qty, 0)>0
        """,
        (activity_id,),
    ).fetchone()[0]

    activity = conn.execute(
        "SELECT COALESCE(baseline_actual_qty, actual_qty, 0) FROM activity WHERE id=?",
        (activity_id,),
    ).fetchone()
    baseline_actual = float(activity[0] or 0) if activity is not None else 0.0
    cumulative_actual = baseline_actual + manual_actual_total
    period_actual = float(excel_period_actual or 0) + float(manual_period_actual or 0)
    return float(planned or 0), period_actual, cumulative_actual


def activity_control(conn: sqlite3.Connection, activity_id: int) -> dict:
    row = conn.execute(
        "SELECT quantity, remaining_qty, daily_target, start_date, finish_date FROM activity WHERE id=?",
        (activity_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Activity not found.")

    planned, period_actual, cumulative_actual = planned_actual_totals(conn, activity_id)
    total = float(row["quantity"] or 0)
    baseline_qty = planned if planned > 0 else max(0.0, total - float(row["remaining_qty"] or 0))
    variance = period_actual - planned
    achievement = (period_actual / planned * 100.0) if planned else 0.0
    physical = (cumulative_actual / total * 100.0) if total else 0.0

    return {
        "planned_qty": planned,
        # Keep actual_qty as a compatibility alias for cumulative actuals.
        "actual_qty": cumulative_actual,
        "period_actual_qty": period_actual,
        "cumulative_actual_qty": cumulative_actual,
        "variance_qty": variance,
        "achievement_pct": achievement,
        "physical_progress_pct": physical,
        "baseline_qty": baseline_qty,
        "remaining_qty": max(0.0, total - cumulative_actual),
    }


def update_activity_rollup(conn: sqlite3.Connection, activity_id: int) -> dict:
    control = activity_control(conn, activity_id)
    conn.execute(
        """
        UPDATE activity
        SET actual_qty=?, planned_qty=?, progress=?, remaining_qty=?,
            status=CASE
                WHEN ? <= 0 THEN status
                WHEN ? >= 100 THEN 'NORMAL'
                WHEN ? >= 90 THEN 'WARNING'
                ELSE 'CRITICAL'
            END
        WHERE id=?
        """,
        (
            control["actual_qty"],
            control["planned_qty"],
            control["physical_progress_pct"] / 100,
            control["remaining_qty"],
            control["planned_qty"],
            control["achievement_pct"],
            control["achievement_pct"],
            activity_id,
        ),
    )
    return control


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


def scan_formula_errors(workbook, cached_workbook=None) -> list[dict]:
    error_tokens = (
        "#REF!",
        "#DIV/0!",
        "#VALUE!",
        "#NAME?",
        "#N/A",
        "#NUM!",
        "#NULL!",
        "#SPILL!",
        "#CALC!",
    )
    errors = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if value is None:
                    continue
                formula_text = value.upper() if isinstance(value, str) else ""
                candidate = cell.data_type in {"f", "e"} or any(
                    token in formula_text for token in error_tokens
                )
                if not candidate:
                    continue
                cached_cell = (
                    cached_workbook[sheet.title][cell.coordinate]
                    if cached_workbook is not None
                    else None
                )
                cached_value = cached_cell.value if cached_cell is not None else None
                if cell.data_type == "f" and cached_cell is not None and cached_value is None:
                    errors.append(
                        {
                            "title": "فرمول Excel بدون مقدار محاسبه‌شده",
                            "detail": f"{sheet.title}!{cell.coordinate}: formula has no cached result",
                            "severity": "HIGH",
                        }
                    )
                    continue
                texts = [
                    text.upper()
                    for text in (value, cached_value)
                    if isinstance(text, str)
                ]
                found_errors = sorted(
                    {
                        token
                        for token in error_tokens
                        if any(token in text for text in texts)
                    }
                )
                is_error_cell = cell.data_type == "e" or (
                    cached_cell is not None and cached_cell.data_type == "e"
                )
                if not found_errors and not is_error_cell:
                    continue

                error_label = ", ".join(found_errors) if found_errors else "Excel error cell"
                errors.append(
                    {
                        "title": "خطای فرمول Excel",
                        "detail": f"{sheet.title}!{cell.coordinate}: {value} ({error_label})",
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
        "WHERE project_id=? AND source='EXCEL' ORDER BY row_no, id",
        (project_id,),
    ).fetchall()

    created: list[dict] = []
    seen = set()

    for index, current in enumerate(rows):
        current_position = (current["position"] or "").strip()
        current_zone = (current["zone"] or "").strip()
        if not current_position or not current_zone:
            continue
        if not current["start_date"]:
            continue
        try:
            current_start = date.fromisoformat(str(current["start_date"])[:10])
        except ValueError:
            continue

        latest_finish = None
        predecessors = []
        for previous in rows[:index]:
            if (
                (previous["position"] or "").strip() != current_position
                or (previous["zone"] or "").strip() != current_zone
            ):
                continue
            if not previous["finish_date"]:
                continue
            try:
                previous_finish = date.fromisoformat(str(previous["finish_date"])[:10])
            except ValueError:
                continue

            if current_start < previous_finish:
                continue

            if latest_finish is None or previous_finish > latest_finish:
                latest_finish = previous_finish
                predecessors = [previous]
            elif previous_finish == latest_finish:
                predecessors.append(previous)

        for previous in predecessors:
            key = (current["id"], previous["id"])
            if key in seen:
                continue
            seen.add(key)
            previous_finish = date.fromisoformat(str(previous["finish_date"])[:10])
            lag_days = (current_start - previous_finish).days
            conn.execute(
                """
                INSERT OR IGNORE INTO activity_dependency(
                    project_id, activity_id, predecessor_activity_id, relation_type, lag_days, notes, source
                ) VALUES (?, ?, ?, 'finish_to_start', ?, ?, 'INFERRED')
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
