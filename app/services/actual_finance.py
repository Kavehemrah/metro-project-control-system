"""Separate actual finance ledger and activity-level EAC indicators."""

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
            # This is an aggregate Excel summary, not an additional activity budget.
            revenue_summary_reference += amount
        else:
            revenue_unallocated += amount

    cost_summary = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN activity_id IS NOT NULL THEN amount ELSE 0 END),0) AS linked,
            COALESCE(SUM(CASE WHEN activity_id IS NULL THEN amount ELSE 0 END),0) AS unallocated
        FROM cost_entry WHERE project_id=? AND source IN ('EXCEL','MANUAL')
        """,
        (project_id,),
    ).fetchone()

    model_budgets = activity_cost_budgets(conn, project_id)
    rows = conn.execute(
        """
        SELECT a.id, a.quantity,
               COALESCE((SELECT SUM(c.amount) FROM cost_entry c
                         WHERE c.project_id=a.project_id AND c.activity_id=a.id
                           AND c.source IN ('EXCEL','MANUAL')),0) AS cost_entry_budget,
               COALESCE((SELECT SUM(x.amount) FROM actual_finance_entry x
                         WHERE x.project_id=a.project_id AND x.activity_id=a.id
                           AND x.entry_type='COST' AND x.source='MANUAL'),0) AS actual_cost
        FROM activity a WHERE a.project_id=?
        """,
        (project_id,),
    ).fetchall()

    earned_value = 0.0
    assessed_earned_value = 0.0
    assessed_budget = 0.0
    assessed_actual_cost = 0.0
    assessed_eac = 0.0
    assessed_count = 0
    total_linked_cost_budget = 0.0
    for row in rows:
        activity_id = int(row["id"])
        # Unit-cost lines replace the older flat allocation for that activity.
        budget = (
            model_budgets[activity_id]
            if activity_id in model_budgets
            else max(0.0, float(row["cost_entry_budget"] or 0))
        )
        budget = max(0.0, budget)
        total_linked_cost_budget += budget
        actual_cost = max(0.0, float(row["actual_cost"] or 0))
        if budget <= 0:
            continue
        control = activity_control(conn, activity_id)
        progress = min(1.0, max(0.0, float(control["physical_progress_pct"] or 0) / 100.0))
        earned_value += budget * progress
        if actual_cost > 0 and progress > 0:
            assessed_budget += budget
            assessed_actual_cost += actual_cost
            assessed_earned_value += budget * progress
            assessed_eac += actual_cost / progress
            assessed_count += 1

    cost_budget_unallocated = float(cost_summary["unallocated"] or 0)
    unassessed_budget = max(0.0, total_linked_cost_budget - assessed_budget)
    cpi = assessed_earned_value / assessed_actual_cost if assessed_actual_cost > 0 else None
    eac = assessed_eac if assessed_count else None
    return {
        "actual_revenue": float(actual["actual_revenue"] or 0),
        "actual_cost": float(actual["actual_cost"] or 0),
        "actual_net": float(actual["actual_revenue"] or 0) - float(actual["actual_cost"] or 0),
        "unallocated_actual_revenue": float(actual["unallocated_actual_revenue"] or 0),
        "unallocated_actual_cost": float(actual["unallocated_actual_cost"] or 0),
        "unplanned_actual_cost": float(actual["unplanned_actual_cost"] or 0),
        "revenue_budget_linked": revenue_linked,
        "revenue_budget_unallocated": revenue_unallocated,
        "revenue_summary_reference": revenue_summary_reference,
        "revenue_summary_difference": revenue_summary_reference - revenue_linked if revenue_summary_reference else None,
        "cost_budget_linked": total_linked_cost_budget,
        "cost_budget_unallocated": cost_budget_unallocated,
        "earned_value_cost": earned_value,
        "assessed_earned_value_cost": assessed_earned_value,
        "assessed_budget": assessed_budget,
        "assessed_actual_cost": assessed_actual_cost,
        "unassessed_budget": unassessed_budget,
        "cpi": cpi,
        "eac_assessed": eac,
        "etc_assessed": max(0.0, eac - assessed_actual_cost) if eac is not None else None,
        "vac_assessed": assessed_budget - eac if eac is not None else None,
        "assessed_activity_count": assessed_count,
        "coverage_pct": assessed_budget / total_linked_cost_budget * 100.0 if total_linked_cost_budget > 0 else 0.0,
        "eac_is_partial": unassessed_budget > 0.01,
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
    """Insert/update actuals, usually used for exceptional costs rather than daily spend."""
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
