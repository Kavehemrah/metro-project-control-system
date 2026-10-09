"""Separate actual finance ledger and activity-level EAC indicators.

Budget allocations (revenue_entry/cost_entry) are kept separate from recorded
cash/revenue/cost actuals (actual_finance_entry). EAC is only estimated for
activities that have both a linked cost budget and recorded actual cost, with
positive progress. The result reports uncovered budget explicitly.
"""
from __future__ import annotations

import sqlite3

from app.services.control import activity_control


def finance_performance(conn: sqlite3.Connection, project_id: int = 1) -> dict:
    actual = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN entry_type='REVENUE' THEN amount ELSE 0 END),0) AS actual_revenue,
            COALESCE(SUM(CASE WHEN entry_type='COST' THEN amount ELSE 0 END),0) AS actual_cost,
            COALESCE(SUM(CASE WHEN entry_type='REVENUE' AND activity_id IS NULL THEN amount ELSE 0 END),0) AS unallocated_actual_revenue,
            COALESCE(SUM(CASE WHEN entry_type='COST' AND activity_id IS NULL THEN amount ELSE 0 END),0) AS unallocated_actual_cost
        FROM actual_finance_entry WHERE project_id=? AND source='MANUAL'
        """,
        (project_id,),
    ).fetchone()

    budgets = {}
    for table, key in (("revenue_entry", "revenue"), ("cost_entry", "cost")):
        row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN activity_id IS NOT NULL THEN amount ELSE 0 END),0) AS linked,
                COALESCE(SUM(CASE WHEN activity_id IS NULL THEN amount ELSE 0 END),0) AS unallocated
            FROM {table} WHERE project_id=? AND source IN ('EXCEL','MANUAL')
            """,
            (project_id,),
        ).fetchone()
        budgets[key] = {"linked": float(row["linked"] or 0), "unallocated": float(row["unallocated"] or 0)}

    rows = conn.execute(
        """
        SELECT a.id, a.quantity,
               COALESCE((SELECT SUM(c.amount) FROM cost_entry c
                         WHERE c.project_id=a.project_id AND c.activity_id=a.id
                           AND c.source IN ('EXCEL','MANUAL')),0) AS cost_budget,
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
    for row in rows:
        budget = max(0.0, float(row["cost_budget"] or 0))
        actual_cost = max(0.0, float(row["actual_cost"] or 0))
        if budget <= 0:
            continue
        control = activity_control(conn, row["id"])
        progress = min(1.0, max(0.0, float(control["physical_progress_pct"] or 0) / 100.0))
        earned_value += budget * progress
        if actual_cost > 0 and progress > 0:
            assessed_budget += budget
            assessed_actual_cost += actual_cost
            assessed_earned_value += budget * progress
            assessed_eac += actual_cost / progress
            assessed_count += 1

    total_linked_cost_budget = budgets["cost"]["linked"]
    unassessed_budget = max(0.0, total_linked_cost_budget - assessed_budget)
    cpi = assessed_earned_value / assessed_actual_cost if assessed_actual_cost > 0 else None
    eac = assessed_eac if assessed_count else None
    return {
        "actual_revenue": float(actual["actual_revenue"] or 0),
        "actual_cost": float(actual["actual_cost"] or 0),
        "actual_net": float(actual["actual_revenue"] or 0) - float(actual["actual_cost"] or 0),
        "unallocated_actual_revenue": float(actual["unallocated_actual_revenue"] or 0),
        "unallocated_actual_cost": float(actual["unallocated_actual_cost"] or 0),
        "revenue_budget_linked": budgets["revenue"]["linked"],
        "revenue_budget_unallocated": budgets["revenue"]["unallocated"],
        "cost_budget_linked": total_linked_cost_budget,
        "cost_budget_unallocated": budgets["cost"]["unallocated"],
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
    entry_id: int | None = None,
) -> int:
    """Insert/update a manual actual finance entry with basic validation."""
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
    if activity_id is not None:
        exists = conn.execute(
            "SELECT 1 FROM activity WHERE id=? AND project_id=?",
            (activity_id, project_id),
        ).fetchone()
        if exists is None:
            raise ValueError("activity_id does not belong to project")
    values = (
        project_id, activity_id, str(entry_date), str(period or ""),
        normalized_type, str(category).strip(), amount, str(notes or ""),
    )
    if entry_id is None:
        cursor = conn.execute(
            """
            INSERT INTO actual_finance_entry(
                project_id,activity_id,entry_date,period,entry_type,category,amount,notes,source
            ) VALUES(?,?,?,?,?,?,?,?, 'MANUAL')
            """,
            values,
        )
        return int(cursor.lastrowid)
    conn.execute(
        """
        UPDATE actual_finance_entry
        SET project_id=?,activity_id=?,entry_date=?,period=?,entry_type=?,category=?,
            amount=?,notes=?
        WHERE id=? AND project_id=? AND source='MANUAL'
        """,
        (*values, entry_id, project_id),
    )
    return int(entry_id)
