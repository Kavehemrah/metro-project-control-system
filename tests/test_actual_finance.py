from app.db import connect
from app.services.actual_finance import finance_performance, record_actual


def _activity(conn, title, quantity=10, actual_qty=5):
    cursor = conn.execute(
        """
        INSERT INTO activity(
            project_id,title,quantity,actual_qty,baseline_actual_qty,remaining_qty,
            progress,source
        ) VALUES(1,?,?,?,?,?,?, 'MANUAL')
        """,
        (title, quantity, actual_qty, actual_qty, max(0, quantity - actual_qty),
         actual_qty / quantity if quantity else 0),
    )
    return cursor.lastrowid


def test_actual_ledger_builds_forecast_from_unit_budget_plus_unplanned_costs(tmp_path):
    conn = connect(tmp_path / "actual-finance.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Finance test')")
    assessed = _activity(conn, "Activity with actual cost", 10, 5)
    unassessed = _activity(conn, "Activity without actual cost", 20, 0)
    conn.execute(
        "INSERT INTO cost_entry(project_id,activity_id,period,category,amount,source) VALUES(1,?,'Oct','Budget A',1000,'MANUAL')",
        (assessed,),
    )
    revenue_id = record_actual(
        conn, project_id=1, entry_date="2026-10-01", period="Oct",
        entry_type="REVENUE", category="Certified revenue", amount=800,
        activity_id=assessed,
    )
    cost_id = record_actual(
        conn, project_id=1, entry_date="2026-10-02", period="Oct",
        entry_type="COST", category="Paid invoice", amount=300,
        activity_id=assessed,
    )
    extra_cost_id = record_actual(
        conn, project_id=1, entry_date="2026-10-03", period="Oct",
        entry_type="COST", category="لودر اضافی", amount=100,
        activity_id=assessed, is_unplanned=True,
    )
    conn.commit()

    result = finance_performance(conn, 1)
    conn.close()

    assert revenue_id > 0 and cost_id > 0 and extra_cost_id > 0
    assert result["actual_revenue"] == 800
    assert result["actual_cost"] == 400
    assert result["actual_net"] == 400
    assert result["unplanned_actual_cost"] == 100
    assert result["earned_value_cost"] == 500
    assert result["assessed_budget"] == 1000
    assert result["assessed_actual_cost"] == 400
    assert result["eac_assessed"] == 1100
    assert result["etc_assessed"] is None
    assert result["vac_assessed"] == -100
    assert result["unassessed_budget"] == 0
    assert result["budgeted_activity_count"] == 1
    assert result["unbudgeted_activity_count"] == 1
    assert result["cost_model_coverage_pct"] == 50
    assert result["cpi"] is None
    assert result["eac_is_partial"] is True
    assert result["coverage_pct"] == 100


def test_actual_ledger_rejects_invalid_type_negative_amount_and_foreign_activity(tmp_path):
    conn = connect(tmp_path / "actual-validation.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Finance test')")

    for kwargs, message in [
        ({"entry_type": "OTHER", "amount": 10, "activity_id": None}, "entry_type"),
        ({"entry_type": "COST", "amount": -1, "activity_id": None}, "amount"),
        ({"entry_type": "COST", "amount": 10, "activity_id": 9999}, "activity_id"),
    ]:
        try:
            record_actual(
                conn, project_id=1, entry_date="2026-10-01", category="Test",
                period="Oct", **kwargs,
            )
        except ValueError as error:
            assert message in str(error)
        else:
            raise AssertionError(f"Expected validation error mentioning {message}")
    conn.close()


def test_budget_forecast_does_not_require_daily_actual_cost_records(tmp_path):
    conn = connect(tmp_path / "actual-no-eac.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Finance test')")
    activity_id = _activity(conn, "No actual cost", 10, 5)
    conn.execute(
        "INSERT INTO cost_entry(project_id,activity_id,period,category,amount,source) VALUES(1,?,'Oct','Budget',1000,'MANUAL')",
        (activity_id,),
    )
    result = finance_performance(conn, 1)
    conn.close()

    assert result["eac_assessed"] == 1000
    assert result["assessed_budget"] == 1000
    assert result["assessed_activity_count"] == 1
    assert result["assessed_actual_cost"] == 0
    assert result["cpi"] is None
    assert result["etc_assessed"] is None
    assert result["eac_is_partial"] is False


def test_contract_revenue_coverage_excludes_excel_summary_reference_from_activity_total(tmp_path):
    conn = connect(tmp_path / "revenue-coverage.db")
    conn.execute(
        "INSERT INTO project(id,name,contract_value) VALUES(1,'Coverage test',10000)"
    )
    activity_id = _activity(conn, "Activity with revenue basis", 10, 5)
    conn.execute(
        """
        INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source)
        VALUES(1,?,'کل پروژه','درآمد فعالیت',4000,'EXCEL_ACTIVITY')
        """,
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source)
        VALUES(1,?,'دوره گزارش','خلاصه درآمد Excel',10000,'EXCEL')
        """,
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO activity_cost_item(
            project_id,activity_id,category,item_name,unit,
            quantity_per_activity_unit,unit_price,source
        ) VALUES(1,?,'مصالح','ردیف ناقص','کیلوگرم',0,250,'MANUAL')
        """,
        (activity_id,),
    )

    result = finance_performance(conn, 1)
    conn.close()

    assert result["contract_value"] == 10000
    assert result["revenue_budget_linked"] == 4000
    assert result["revenue_summary_reference"] == 10000
    assert result["revenue_coverage_pct"] == 40
    assert result["revenue_contract_difference"] == 6000
    assert result["zero_cost_item_count"] == 1
