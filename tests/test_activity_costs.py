from app.db import connect
from app.services.activity_costs import (
    activity_cost_budgets,
    activity_cost_items,
    activity_resource_forecast,
    sync_activity_revenue_from_physical,
)
from app.services.forecast import forecast_finance


def _activity(conn, title="بتن‌ریزی اسلب", quantity=10, daily_target=2, actual=0):
    cursor = conn.execute(
        """
        INSERT INTO activity(
            project_id,row_no,position,zone,title,unit,quantity,remaining_qty,
            baseline_actual_qty,actual_qty,progress,daily_target,source
        ) VALUES(1,1,'تونل ۴','خط شرقی',?,'اسلب',?,?, ?,?, ?,?,'MANUAL')
        """,
        (title, quantity, max(0, quantity - actual), actual, actual,
         actual / quantity if quantity else 0, daily_target),
    )
    return cursor.lastrowid


def test_activity_cost_budget_is_unit_coefficient_times_unit_price_times_activity_quantity(tmp_path):
    conn = connect(tmp_path / "cost-build-up.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Test')")
    activity_id = _activity(conn, quantity=10)
    conn.execute(
        """
        INSERT INTO activity_cost_item(
            project_id,activity_id,category,item_name,unit,
            quantity_per_activity_unit,unit_price,source
        ) VALUES(1,?,'مصالح','بتن','مترمکعب',0.35,2000000,'MANUAL')
        """,
        (activity_id,),
    )

    budgets = activity_cost_budgets(conn, 1)
    lines = activity_cost_items(conn, 1)
    conn.close()

    assert budgets[activity_id] == 7000000
    assert len(lines) == 1
    assert lines[0]["estimated_total"] == 7000000


def test_resource_forecast_uses_peak_daily_capacity_for_workforce_and_equipment(tmp_path):
    conn = connect(tmp_path / "capacity.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Test')")
    activity_id = _activity(conn, quantity=10, daily_target=2)

    labor_id = conn.execute(
        """
        INSERT INTO resource(project_id,category,title,required,available,unit,unit_price,source)
        VALUES(1,'نیروی انسانی','آرماتوربند',0,3,'نفر',100,'MANUAL')
        """
    ).lastrowid
    conn.executemany(
        """
        INSERT INTO activity_daily_plan(activity_id,plan_date,quantity,source)
        VALUES(?,?,?,'MANUAL')
        """,
        [(activity_id, "2026-10-01", 1), (activity_id, "2026-10-02", 2)],
    )
    conn.execute(
        """
        INSERT INTO activity_resource_requirement(
            project_id,activity_id,resource_id,category,resource_title,unit,
            quantity_per_activity_unit,source
        ) VALUES(1,?,?,'نیروی انسانی','آرماتوربند','نفر',2.5,'MANUAL')
        """,
        (activity_id, labor_id),
    )

    rows = activity_resource_forecast(conn, 1)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["period"] == "مهر 1405"
    assert rows[0]["planned_activity_qty"] == 2
    assert rows[0]["required_qty"] == 5
    assert rows[0]["available_qty"] == 3
    assert rows[0]["shortage_qty"] == 2
    assert rows[0]["shortage_cost"] == 200


def test_material_requirement_sums_planned_quantity_over_reporting_period(tmp_path):
    conn = connect(tmp_path / "material-demand.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Test')")
    activity_id = _activity(conn, quantity=10, daily_target=2)
    cement_id = conn.execute(
        """
        INSERT INTO resource(project_id,category,title,required,available,unit,unit_price,source)
        VALUES(1,'مصالح','سیمان',0,10,'کیلوگرم',50,'MANUAL')
        """
    ).lastrowid
    conn.executemany(
        """
        INSERT INTO activity_daily_plan(activity_id,plan_date,quantity,source)
        VALUES(?,?,?,'MANUAL')
        """,
        [(activity_id, "2026-10-01", 1), (activity_id, "2026-10-02", 2)],
    )
    conn.execute(
        """
        INSERT INTO activity_resource_requirement(
            project_id,activity_id,resource_id,category,resource_title,unit,
            quantity_per_activity_unit,source
        ) VALUES(1,?,?,'مصالح','سیمان','کیلوگرم',5,'MANUAL')
        """,
        (activity_id, cement_id),
    )

    rows = activity_resource_forecast(conn, 1)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["planned_activity_qty"] == 3
    assert rows[0]["required_qty"] == 15
    assert rows[0]["shortage_qty"] == 5
    assert rows[0]["shortage_cost"] == 250


def test_resource_capacity_is_not_compared_to_total_activity_volume_without_daily_basis(tmp_path):
    conn = connect(tmp_path / "unknown-capacity.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Test')")
    activity_id = _activity(conn, quantity=100, daily_target=0)
    resource_id = conn.execute(
        "INSERT INTO resource(project_id,category,title,available,unit,source) VALUES(1,'ماشین‌آلات','لودر',2,'دستگاه','MANUAL')"
    ).lastrowid
    conn.execute(
        """
        INSERT INTO activity_resource_requirement(
            project_id,activity_id,resource_id,category,resource_title,unit,
            quantity_per_activity_unit,source
        ) VALUES(1,? ,?,'ماشین‌آلات','لودر','دستگاه',1,'MANUAL')
        """,
        (activity_id, resource_id),
    )

    rows = activity_resource_forecast(conn, 1)
    conn.close()

    assert rows[0]["availability_basis"] == "daily_capacity_unknown"
    assert rows[0]["shortage_qty"] is None


def test_excel_physical_unit_rate_links_revenue_to_activity_without_double_counting_summary(tmp_path):
    conn = connect(tmp_path / "activity-revenue.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Test')")
    activity_id = _activity(conn, quantity=10, actual=5)
    conn.execute(
        "INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source) VALUES(1,NULL,'مهر 1405','درآمد خلاصه',1000,'EXCEL')"
    )
    physical = {
        ("تونل4", "خطشرقی", "بتن ریزی اسلب"): {
            "title": "بتن‌ریزی اسلب", "unit_rate": 25, "total": 10,
        }
    }
    mapping = {("تونل4", "خطشرقی", "بتن ریزی اسلب"): activity_id}

    assert sync_activity_revenue_from_physical(conn, physical, mapping) == 1
    assert sync_activity_revenue_from_physical(conn, physical, mapping) == 1
    conn.commit()

    forecast = forecast_finance(conn, 1)["revenue"]
    conn.close()

    assert forecast["linked_budget"] == 250
    assert forecast["earned_to_date"] == 125
    assert forecast["summary_reference_amount"] == 1000
    assert forecast["summary_reconciliation_difference"] == 750
    assert forecast["unallocated_amount"] == 0
