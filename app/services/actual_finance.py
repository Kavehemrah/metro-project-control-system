"""Actual finance ledger plus budget-based cost forecast.

The cost forecast deliberately does not require daily booking of ordinary costs.
Activity budgets come from unit-cost build-ups (or legacy activity-linked cost
budgets); flagged unexpected costs are added to the estimated cost subtotal.
CPI/remaining-cost calculations are withheld because ordinary actual expenditure
is not guaranteed to be recorded comprehensively.
"""

from __future__ import annotations

import sqlite3

from app.services.activity_costs import activity_cost_budgets
from app.services.control import activity_control
from app.services.dates import normalize_date


def finance_performance(conn: sqlite3.Connection, project_id: int = 1) -> dict:
    actual = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN entry_type='REVENUE' THEN amount ELSE 0 END),0) AS actual_revenue,
            COALESCE(SUM(CASE WHEN entry_type='COST' THEN amount ELSE 0 END),0) AS actual_cost,
            COALESCE(SUM(CASE WHEN entry_type='REVENUE' AND activity_id IS NULL THEN amount ELSE 0 END),0) AS unallocated_actual_revenue,
            COALESCE(SUM(CASE WHEN entry_type='COST' AND activity_id IS NULL THEN amount ELSE 0 END),0) AS unallocated_actual_cost,
            COALESCE(SUM(CASE WHEN entry_type='COST' AND is_unplanned=1 THEN amount ELSE 0 END),0) AS unplanned_actual_cost
        FROM actual_finance_entry WHERE project_id=? AND source='MANUAL'
        """,
        (project_id,),
    ).fetchone()

    revenue_rows = conn.execute(
        """
        SELECT activity_id, amount, source FROM revenue_entry
        WHERE project_id=? AND source IN ('EXCEL','MANUAL','EXCEL_ACTIVITY')
        """,
        (project_id,),
    ).fetchall()
    physical_revenue_available = any(
        row["source"] == "EXCEL_ACTIVITY" and row["activity_id"] is not None
        for row in revenue_rows
    )
    revenue_linked = 0.0
    revenue_unallocated = 0.0
    revenue_summary_reference = 0.0
    for row in revenue_rows:
        amount = float(row["amount"] or 0)
        if row["activity_id"] is not None:
            revenue_linked += amount
        elif physical_revenue_available and row["source"] == "EXCEL":
            # Aggregate Excel period figures are references, not additional activity budgets.
            revenue_summary_reference += amount
        else:
            revenue_unallocated += amount

    cost_rows = conn.execute(
        """
        SELECT a.id,
               COALESCE((SELECT SUM(c.amount) FROM cost_entry c
                         WHERE c.project_id=a.project_id AND c.activity_id=a.id
                           AND c.source IN ('EXCEL','MANUAL')),0) AS cost_entry_budget,
               COALESCE((SELECT SUM(x.amount) FROM actual_finance_entry x
                         WHERE x.project_id=a.project_id AND x.activity_id=a.id
                           AND x.entry_type='COST' AND x.source='MANUAL'),0) AS actual_cost,
               COALESCE((SELECT SUM(x.amount) FROM actual_finance_entry x
                         WHERE x.project_id=a.project_id AND x.activity_id=a.id
                           AND x.entry_type='COST' AND x.source='MANUAL'
                           AND x.is_unplanned=1),0) AS unplanned_cost
        FROM activity a WHERE a.project_id=?
        """,
        (project_id,),
    ).fetchall()

    unit_cost_budgets = activity_cost_budgets(conn, project_id)
    total_activity_count = int(
        conn.execute("SELECT COUNT(*) FROM activity WHERE project_id=?", (project_id,)).fetchone()[0]
    )
    budgeted_activity_count = 0
    base_cost_budget = 0.0
    earned_value = 0.0
    assessed_actual_cost = 0.0
    unplanned_cost_applied = 0.0
    unplanned_cost_on_unbudgeted_activities = 0.0

    for row in cost_rows:
        activity_id = int(row["id"])
        # Unit build-ups are authoritative when present; otherwise retain a legacy
        # explicit activity-linked cost budget, without mixing the two.
        budget = (
            unit_cost_budgets[activity_id]
            if activity_id in unit_cost_budgets
            else max(0.0, float(row["cost_entry_budget"] or 0))
        )
        actual_cost = max(0.0, float(row["actual_cost"] or 0))
        unplanned_cost = max(0.0, float(row["unplanned_cost"] or 0))
        assessed_actual_cost += actual_cost
        if budget <= 0:
            unplanned_cost_on_unbudgeted_activities += unplanned_cost
            continue

        budgeted_activity_count += 1
        base_cost_budget += budget
        control = activity_control(conn, activity_id)
        progress = min(1.0, max(0.0, float(control["physical_progress_pct"] or 0) / 100.0))
        earned_value += budget * progress
        unplanned_cost_applied += unplanned_cost

    cost_summary = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN activity_id IS NOT NULL THEN amount ELSE 0 END),0) AS linked,
            COALESCE(SUM(CASE WHEN activity_id IS NULL AND source='EXCEL' THEN amount ELSE 0 END),0) AS excel_reference,
            COALESCE(SUM(CASE WHEN activity_id IS NULL AND source<>'EXCEL' THEN amount ELSE 0 END),0) AS unallocated
        FROM cost_entry
        WHERE project_id=? AND source IN ('EXCEL','MANUAL')
        """,
        (project_id,),
    ).fetchone()
    has_activity_cost_budget = base_cost_budget > 0
    cost_summary_reference = (
        float(cost_summary["excel_reference"] or 0) if has_activity_cost_budget else 0.0
    )
    cost_budget_unallocated = float(cost_summary["unallocated"] or 0)
    if not has_activity_cost_budget:
        cost_budget_unallocated += float(cost_summary["excel_reference"] or 0)

    forecast_cost_assessed = (
        base_cost_budget + unplanned_cost_applied if budgeted_activity_count else None
    )
    cost_model_coverage_pct = (
        budgeted_activity_count / total_activity_count * 100.0 if total_activity_count else 0.0
    )
    eac_is_partial = (
        budgeted_activity_count < total_activity_count
        or unplanned_cost_on_unbudgeted_activities > 0
        or cost_budget_unallocated > 0
        or float(actual["unallocated_actual_cost"] or 0) > 0
    )

    return {
        "actual_revenue": float(actual["actual_revenue"] or 0),
        "actual_cost": float(actual["actual_cost"] or 0),
        "actual_net": float(actual["actual_revenue"] or 0) - float(actual["actual_cost"] or 0),
        "unallocated_actual_revenue": float(actual["unallocated_actual_revenue"] or 0),
        "unallocated_actual_cost": float(actual["unallocated_actual_cost"] or 0),
        "unplanned_actual_cost": float(actual["unplanned_actual_cost"] or 0),
        "unplanned_cost_applied_to_forecast": unplanned_cost_applied,
        "unplanned_cost_on_unbudgeted_activities": unplanned_cost_on_unbudgeted_activities,
        "revenue_budget_linked": revenue_linked,
        "revenue_budget_unallocated": revenue_unallocated,
        "revenue_summary_reference": revenue_summary_reference,
        "revenue_summary_difference": revenue_summary_reference - revenue_linked if revenue_summary_reference else None,
        "cost_budget_linked": base_cost_budget,
        "cost_budget_unallocated": cost_budget_unallocated,
        "cost_summary_reference": cost_summary_reference,
        "cost_summary_difference": cost_summary_reference - base_cost_budget if cost_summary_reference else None,
        "earned_value_cost": earned_value,
        "assessed_earned_value_cost": earned_value,
        "assessed_budget": base_cost_budget,
        "assessed_actual_cost": assessed_actual_cost,
        "unassessed_budget": 0.0,
        "cpi": None,
        "eac_assessed": forecast_cost_assessed,
        # ETC/CPI are intentionally not estimated from an exception-only ledger.
        "etc_assessed": None,
        "vac_assessed": base_cost_budget - forecast_cost_assessed if forecast_cost_assessed is not None else None,
        "assessed_activity_count": budgeted_activity_count,
        "total_activity_count": total_activity_count,
        "budgeted_activity_count": budgeted_activity_count,
        "unbudgeted_activity_count": max(0, total_activity_count - budgeted_activity_count),
        "cost_model_coverage_pct": cost_model_coverage_pct,
        "coverage_pct": (
            base_cost_budget / (base_cost_budget + cost_budget_unallocated) * 100.0
            if base_cost_budget + cost_budget_unallocated > 0 else 0.0
        ),
        "eac_is_partial": eac_is_partial,
    }


def record_actual(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    entry_date: str,
    entry_type: str,
    category: str,
    amount: float,
    period: str = "",
    activity_id: int | None = None,
    notes: str = "",
    is_unplanned: bool = False,
    entry_id: int | None = None,
) -> int:
    """Insert/update actual or exceptional financial entries; daily regular posting is not required."""
    normalized_type = str(entry_type).upper()
    if normalized_type not in {"REVENUE", "COST"}:
        raise ValueError("entry_type must be REVENUE or COST")
    if not str(entry_date).strip():
        raise ValueError("entry_date is required")
    if not str(category).strip():
        raise ValueError("category is required")
    amount = float(amount)
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if is_unplanned and normalized_type != "COST":
        raise ValueError("unplanned flag is only valid for cost entries")
    if activity_id is not None:
        exists = conn.execute(
            "SELECT 1 FROM activity WHERE id=? AND project_id=?",
            (activity_id, project_id),
        ).fetchone()
        if exists is None:
            raise ValueError("activity_id does not belong to project")

    normalized_date = normalize_date(entry_date)
    values = (
        project_id, activity_id, normalized_date, str(period or ""),
        normalized_type, str(category).strip(), amount, str(notes or ""), int(bool(is_unplanned)),
    )
    if entry_id is None:
        cursor = conn.execute(
            """
            INSERT INTO actual_finance_entry(
                project_id,activity_id,entry_date,period,entry_type,category,amount,notes,is_unplanned,source
            ) VALUES(?,?,?,?,?,?,?,?,?, 'MANUAL')
            """,
            values,
        )
        return int(cursor.lastrowid)
    conn.execute(
        """
        UPDATE actual_finance_entry
        SET project_id=?,activity_id=?,entry_date=?,period=?,entry_type=?,category=?,
            amount=?,notes=?,is_unplanned=?
        WHERE id=? AND project_id=? AND source='MANUAL'
        """,
        (*values, entry_id, project_id),
    )
    return int(entry_id)
