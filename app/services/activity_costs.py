"""Unit-cost and activity-based resource planning services."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date

import jdatetime

from app.services.dates import normalize_date


_PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def _report_period(value: str) -> str | None:
    try:
        gregorian = date.fromisoformat(normalize_date(value))
        jalali = jdatetime.date.fromgregorian(date=gregorian)
        return f"{_PERSIAN_MONTHS[jalali.month - 1]} {jalali.year}"
    except (ValueError, TypeError):
        return None


def activity_cost_items(conn: sqlite3.Connection, project_id: int = 1) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ci.*, a.row_no, a.position, a.zone, a.title AS activity_title,
               a.unit AS activity_unit, a.quantity AS activity_quantity,
               COALESCE(a.quantity,0) * COALESCE(ci.quantity_per_activity_unit,0)
                   * COALESCE(ci.unit_price,0) AS estimated_total
        FROM activity_cost_item ci
        JOIN activity a ON a.id=ci.activity_id AND a.project_id=ci.project_id
        WHERE ci.project_id=?
        ORDER BY a.row_no, a.id, ci.category, ci.item_name
        """,
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def activity_cost_budgets(conn: sqlite3.Connection, project_id: int = 1) -> dict[int, float]:
    totals: dict[int, float] = defaultdict(float)
    for row in conn.execute(
        """
        SELECT ci.activity_id,
               SUM(COALESCE(a.quantity,0) * COALESCE(ci.quantity_per_activity_unit,0)
                   * COALESCE(ci.unit_price,0)) AS total
        FROM activity_cost_item ci
        JOIN activity a ON a.id=ci.activity_id AND a.project_id=ci.project_id
        WHERE ci.project_id=?
        GROUP BY ci.activity_id
        """,
        (project_id,),
    ).fetchall():
        total = max(0.0, float(row["total"] or 0))
        if total > 0:
            totals[int(row["activity_id"])] = total
    return dict(totals)


def sync_activity_revenue_from_physical(
    conn: sqlite3.Connection,
    physical_items: dict,
    activity_ids_by_key: dict[tuple[str, str, str], int],
    project_id: int = 1,
) -> int:
    """Create per-activity revenue budgets using Excel unit-rate × total quantity.

    Keys must be the normalized physical-progress keys used by the Excel importer.
    Summary revenue rows are kept unchanged as project-level reference figures.
    """
    conn.execute(
        "DELETE FROM revenue_entry WHERE project_id=? AND source='EXCEL_ACTIVITY'",
        (project_id,),
    )
    created = 0
    for key, item in physical_items.items():
        activity_id = activity_ids_by_key.get(key)
        unit_rate = max(0.0, float(item.get("unit_rate") or 0))
        total_qty = max(0.0, float(item.get("total") or 0))
        if unit_rate <= 0 or total_qty <= 0:
            continue
        if activity_id is None:
            conn.execute(
                """
                INSERT INTO discrepancy(project_id,title,detail,severity,source)
                VALUES(?,?,?,?, 'EXCEL')
                """,
                (
                    project_id,
                    "درآمد فعالیت بدون تطبیق",
                    f"ردیف شیت پیشرفت فیزیکی {item.get('row_no', '?')}: «{item.get('position', '')} / {item.get('zone', '')} / {item.get('title', '')}» دارای بهای واحد درآمد و حجم است، اما فعالیت متناظر پیدا نشد؛ مبلغ به فعالیتی نسبت داده نشد.",
                    "MEDIUM",
                ),
            )
            continue
        amount = unit_rate * total_qty
        conn.execute(
            """
            INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source)
            VALUES(?,?,?,?,?,'EXCEL_ACTIVITY')
            """,
            (
                project_id,
                activity_id,
                "بودجه کل فعالیت",
                f"درآمد مبنای فعالیت: {item.get('title') or 'فعالیت'}",
                amount,
            ),
        )
        created += 1
    return created


def activity_resource_forecast(conn: sqlite3.Connection, project_id: int = 1) -> list[dict]:
    """Calculate per-unit resource demand; use peak daily demand for workforce/equipment."""
    requirements = conn.execute(
        """
        SELECT rr.*, a.row_no, a.position, a.zone, a.title AS activity_title,
               a.unit AS activity_unit, a.quantity AS activity_quantity,
               a.daily_target AS activity_daily_target,
               r.title AS current_resource_title, r.available AS resource_available,
               r.unit AS resource_unit
        FROM activity_resource_requirement rr
        JOIN activity a ON a.id=rr.activity_id AND a.project_id=rr.project_id
        LEFT JOIN resource r ON r.id=rr.resource_id AND r.project_id=rr.project_id
        WHERE rr.project_id=?
        ORDER BY a.row_no, a.id, rr.category, rr.resource_title
        """,
        (project_id,),
    ).fetchall()

    results = []
    for requirement in requirements:
        item = dict(requirement)
        rate = max(0.0, float(item["quantity_per_activity_unit"] or 0))
        activity_id = int(item["activity_id"])
        is_capacity = item["category"] in {"نیروی انسانی", "ماشین‌آلات"}
        plan_rows = conn.execute(
            """
            SELECT plan_date, SUM(quantity) AS quantity
            FROM activity_daily_plan
            WHERE activity_id=? AND source IN ('EXCEL','MANUAL')
            GROUP BY plan_date ORDER BY plan_date
            """,
            (activity_id,),
        ).fetchall()

        period_qty: dict[str, float] = defaultdict(float)
        for plan in plan_rows:
            day_qty = max(0.0, float(plan["quantity"] or 0))
            period = _report_period(str(plan["plan_date"])) or "دوره نامشخص"
            if is_capacity:
                # People and equipment are concurrent capacity, not monthly consumption:
                # the peak planned daily output is the appropriate comparison.
                period_qty[period] = max(period_qty[period], day_qty)
            else:
                period_qty[period] += day_qty

        capacity_is_unknown = False
        if not period_qty:
            if is_capacity and float(item["activity_daily_target"] or 0) > 0:
                period_qty["برآورد بر مبنای راندمان روزانه"] = max(
                    0.0, float(item["activity_daily_target"])
                )
            elif is_capacity:
                period_qty["ظرفیت روزانه نامشخص"] = max(
                    0.0, float(item["activity_quantity"] or 0)
                )
                capacity_is_unknown = True
            else:
                period_qty["کل حجم فعالیت"] = max(
                    0.0, float(item["activity_quantity"] or 0)
                )

        supply_periods = {}
        if item["resource_id"] is not None:
            for supply in conn.execute(
                """
                SELECT period, available_qty, opening_stock, purchase_qty, unit_price
                FROM resource_period
                WHERE resource_id=? AND source IN ('EXCEL','MANUAL')
                """,
                (item["resource_id"],),
            ).fetchall():
                supply_periods[str(supply["period"])] = dict(supply)

        default_available = item["resource_available"]
        carry_inventory = (
            max(0.0, float(default_available))
            if not is_capacity and default_available is not None and not supply_periods
            else None
        )
        for period, planned_activity_qty in period_qty.items():
            required_qty = rate * planned_activity_qty
            supply = supply_periods.get(period)
            available = None
            availability_basis = "unknown"
            if capacity_is_unknown:
                # A total activity volume is not a daily workforce/equipment count.
                availability_basis = "daily_capacity_unknown"
            elif supply and supply["available_qty"] is not None:
                available = max(0.0, float(supply["available_qty"]))
                availability_basis = "available_qty"
            elif supply and supply["opening_stock"] is not None and supply["purchase_qty"] is not None:
                available = max(
                    0.0,
                    float(supply["opening_stock"] or 0) + float(supply["purchase_qty"] or 0),
                )
                availability_basis = "opening_stock+purchase_qty"
            elif is_capacity and default_available is not None:
                available = max(0.0, float(default_available))
                availability_basis = "resource.available"
            elif carry_inventory is not None:
                available = carry_inventory
                availability_basis = "opening_inventory_carried_forward"
            elif not is_capacity and supply_periods:
                available = None
                availability_basis = "period_supply_not_defined"
            elif default_available is not None:
                available = max(0.0, float(default_available))
                availability_basis = "resource.available"

            shortage = max(0.0, required_qty - available) if available is not None else None
            if carry_inventory is not None:
                # Treat the card's available quantity as total material stock for the
                # whole horizon. Consume each period's requirement once to avoid
                # showing the same inventory as available in every month.
                carry_inventory = max(0.0, carry_inventory - required_qty)
            price = None
            if supply and supply.get("unit_price") is not None:
                price = float(supply["unit_price"])
            elif item.get("resource_id") is not None:
                resource_price = conn.execute(
                    "SELECT unit_price FROM resource WHERE id=? AND project_id=?",
                    (item["resource_id"], project_id),
                ).fetchone()
                if resource_price and resource_price["unit_price"] is not None:
                    price = float(resource_price["unit_price"])
            results.append(
                {
                    **item,
                    "period": period,
                    "is_capacity_resource": is_capacity,
                    "planned_activity_qty": planned_activity_qty,
                    "required_qty": required_qty,
                    "available_qty": available,
                    "shortage_qty": shortage,
                    "availability_basis": availability_basis,
                    "shortage_cost": shortage * price if shortage is not None and price is not None else None,
                }
            )
    return results
