from datetime import date

from app.db import connect
from app.services.forecast import calculate_schedule, forecast_finance, resource_shortages


def _add_activity(conn, title, start, finish, quantity=10, baseline_actual=0):
    cursor = conn.execute(
        """
        INSERT INTO activity(
            project_id,title,quantity,remaining_qty,baseline_actual_qty,actual_qty,
            start_date,finish_date,duration_days,daily_target,source
        ) VALUES(1,?,?,?,?,?,?,?,?,?,'MANUAL')
        """,
        (
            title, quantity, max(0, quantity - baseline_actual), baseline_actual,
            baseline_actual, start, finish,
            (date.fromisoformat(finish) - date.fromisoformat(start)).days + 1,
            2,
        ),
    )
    return cursor.lastrowid


def test_schedule_propagates_actual_delay_through_finish_to_start_chain(tmp_path):
    conn = connect(tmp_path / "schedule.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Schedule test')")
    first = _add_activity(conn, "A", "2026-10-01", "2026-10-03", 10, 0)
    second = _add_activity(conn, "B", "2026-10-04", "2026-10-06", 10, 0)
    third = _add_activity(conn, "C", "2026-10-07", "2026-10-08", 10, 0)
    conn.executemany(
        """
        INSERT INTO activity_dependency(
            project_id,activity_id,predecessor_activity_id,relation_type,lag_days,source
        ) VALUES(1,?,?,'finish_to_start',?,'MANUAL')
        """,
        [(second, first, 1), (third, second, 1)],
    )
    conn.execute(
        "INSERT INTO activity_daily_actual(activity_id,actual_date,quantity,source) VALUES(?,?,?,'MANUAL')",
        (first, "2026-10-04", 10),
    )

    result = calculate_schedule(conn, 1, as_of=date(2026, 10, 9))
    dates = {item["title"]: item for item in result["activities"]}
    conn.commit()
    conn.close()

    assert result["cycle"] == []
    assert dates["A"]["forecast_finish"].isoformat() == "2026-10-04"
    assert dates["B"]["forecast_start"].isoformat() == "2026-10-05"
    assert dates["B"]["forecast_finish"].isoformat() == "2026-10-07"
    assert dates["C"]["forecast_start"].isoformat() == "2026-10-08"
    assert dates["C"]["forecast_finish"].isoformat() == "2026-10-09"
    assert dates["C"]["delay_days"] == 1


def test_schedule_detects_cycles_without_persisting_partial_results(tmp_path):
    conn = connect(tmp_path / "cycle.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Cycle test')")
    a = _add_activity(conn, "A", "2026-10-01", "2026-10-02")
    b = _add_activity(conn, "B", "2026-10-03", "2026-10-04")
    conn.executemany(
        """
        INSERT INTO activity_dependency(
            project_id,activity_id,predecessor_activity_id,relation_type,lag_days,source
        ) VALUES(1,?,?,'finish_to_start',0,'MANUAL')
        """,
        [(a, b), (b, a)],
    )
    result = calculate_schedule(conn, 1)
    stored = conn.execute(
        "SELECT forecast_finish FROM activity WHERE id=?", (a,)
    ).fetchone()[0]
    conn.close()

    assert result["cycle"]
    assert set(result["cycle"]) == {a, b}
    assert stored is None


def test_finance_forecast_allocates_only_activity_linked_amounts(tmp_path):
    conn = connect(tmp_path / "finance.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Finance test')")
    activity_id = _add_activity(conn, "Half done", "2026-10-01", "2026-10-10", 10, 5)
    conn.execute(
        "INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source) VALUES(1,?,'Oct','Activity revenue',1000,'MANUAL')",
        (activity_id,),
    )
    conn.execute(
        "INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source) VALUES(1,NULL,'Oct','Unallocated revenue',400,'EXCEL')"
    )
    conn.execute(
        "INSERT INTO cost_entry(project_id,activity_id,period,category,amount,source) VALUES(1,?,'Oct','Activity cost',2000,'MANUAL')",
        (activity_id,),
    )

    result = forecast_finance(conn, 1)
    conn.close()

    assert result["revenue"]["linked_budget"] == 1000
    assert result["revenue"]["earned_to_date"] == 500
    assert result["revenue"]["forecast_at_completion"] == 1000
    assert result["revenue"]["unallocated_amount"] == 400
    assert result["revenue"]["coverage_pct"] == 1000 / 1400 * 100
    assert result["cost"]["linked_budget"] == 2000
    assert result["cost"]["earned_to_date"] == 1000


def test_resource_shortage_uses_available_quantity_or_stock_plus_purchase(tmp_path):
    conn = connect(tmp_path / "resources.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Resource test')")
    first = conn.execute(
        "INSERT INTO resource(project_id,category,title,required,available,unit,unit_price,source) VALUES(1,'مصالح','سیمان',20,0,'تن',4,'MANUAL')"
    ).lastrowid
    second = conn.execute(
        "INSERT INTO resource(project_id,category,title,required,available,unit,unit_price,source) VALUES(1,'مصالح','ماسه',20,0,'تن',2,'MANUAL')"
    ).lastrowid
    conn.execute(
        """
        INSERT INTO resource_period(
            resource_id,period,required_qty,available_qty,opening_stock,purchase_qty,unit_price,source
        ) VALUES(?, 'مهر',20,NULL,10,5,4,'MANUAL')
        """,
        (first,),
    )
    conn.execute(
        """
        INSERT INTO resource_period(
            resource_id,period,required_qty,available_qty,opening_stock,purchase_qty,unit_price,source
        ) VALUES(?, 'مهر',20,25,10,15,2,'MANUAL')
        """,
        (second,),
    )

    results = {item["title"]: item for item in resource_shortages(conn, 1)}
    conn.close()

    assert results["سیمان"]["available_qty"] == 15
    assert results["سیمان"]["shortage_qty"] == 5
    assert results["سیمان"]["shortage_cost"] == 20
    assert results["ماسه"]["available_qty"] == 25
    assert results["ماسه"]["shortage_qty"] == 0
