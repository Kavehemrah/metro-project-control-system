from pathlib import Path
import math

from openpyxl import load_workbook

from app.db import connect
from imports.import_excel import _parse_detail_cost, _parse_physical_progress, import_workbook


def test_workbook_import_includes_resource_sheets():
    workbook = Path(__file__).resolve().parents[1] / "metro-project-control-system.xlsx"
    assert workbook.exists(), "Workbook should exist in project root"
    workbook_values = load_workbook(workbook, data_only=True, read_only=True)
    detail_cost = _parse_detail_cost(workbook_values)
    physical_entries_expected = sum(
        max(1, len(item["periods"]))
        for item in _parse_physical_progress(workbook_values).values()
    )
    workbook_values.close()
    workbook_formulas = load_workbook(workbook, data_only=False, read_only=True)
    formula_sheet = workbook_formulas.worksheets[3]
    assert formula_sheet["S50"].value == "=I13*S46"
    assert formula_sheet["T50"].value == "=S46*J13"
    assert formula_sheet["S52"].value == "=I15*S46"
    assert formula_sheet["T52"].value == "=S46*J15"
    assert formula_sheet["T58"].value == "=#REF!+J11"
    workbook_formulas.close()

    conn = connect()
    conn.execute("DELETE FROM resource WHERE project_id=1")
    conn.execute("DELETE FROM discrepancy WHERE project_id=1")
    conn.execute("DELETE FROM activity WHERE project_id=1")
    conn.execute("DELETE FROM activity_daily_plan WHERE activity_id IN (SELECT id FROM activity WHERE project_id=1)")
    conn.execute("DELETE FROM activity_period WHERE activity_id IN (SELECT id FROM activity WHERE project_id=1)")
    conn.execute("DELETE FROM monthly_finance WHERE project_id=1")
    conn.execute("DELETE FROM kpi WHERE project_id=1")
    conn.execute("DELETE FROM project WHERE id=1")
    conn.commit()
    conn.close()

    import_workbook(str(workbook))

    conn = connect()
    resources = conn.execute("SELECT COUNT(*) FROM resource WHERE project_id=1").fetchone()[0]
    activities = conn.execute("SELECT COUNT(*) FROM activity WHERE project_id=1").fetchone()[0]
    risks = conn.execute("SELECT COUNT(*) FROM risk WHERE project_id=1").fetchone()[0]
    physical_entries = conn.execute(
        "SELECT COUNT(*) FROM physical_progress_entry WHERE project_id=1"
    ).fetchone()[0]
    physical_mapping_warnings = conn.execute(
        "SELECT COUNT(*) FROM discrepancy WHERE project_id=1 AND title='پیشرفت فیزیکی بدون تطبیق یک‌به‌یک'"
    ).fetchone()[0]
    formula_errors = conn.execute(
        "SELECT COUNT(*) FROM discrepancy WHERE project_id=1 AND title='خطای فرمول Excel'"
    ).fetchone()[0]
    formula_error_details = [
        row[0]
        for row in conn.execute(
            "SELECT detail FROM discrepancy WHERE project_id=1 AND title='خطای فرمول Excel'"
        )
    ]
    risk_score_mismatches = conn.execute(
        "SELECT COUNT(*) FROM risk WHERE project_id=1 AND score<>probability*impact*control"
    ).fetchone()[0]
    database_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    first_risk = conn.execute(
        "SELECT category,probability,impact,control,score,consequence,existing_controls "
        "FROM risk WHERE project_id=1 ORDER BY id LIMIT 1"
    ).fetchone()
    manpower = conn.execute("SELECT COUNT(*) FROM resource WHERE project_id=1 AND category='نیروی انسانی'").fetchone()[0]
    tunnel4 = conn.execute(
        "SELECT COUNT(*) FROM activity WHERE project_id=1 AND (position LIKE '%تونل%' OR zone LIKE '%تونل%' OR title LIKE '%تونل%') AND (position LIKE '%4%' OR zone LIKE '%4%' OR title LIKE '%4%')"
    ).fetchone()[0]
    revenue_entries = conn.execute("SELECT COUNT(*) FROM revenue_entry WHERE project_id=1").fetchone()[0]
    cost_entries = conn.execute("SELECT COUNT(*) FROM cost_entry WHERE project_id=1").fetchone()[0]
    dependencies = conn.execute("SELECT COUNT(*) FROM activity_dependency WHERE project_id=1").fetchone()[0]
    monthly = {
        row["month"]: (row["revenue"], row["cost"])
        for row in conn.execute("SELECT month,revenue,cost FROM monthly_finance WHERE project_id=1")
    }
    financial_totals = conn.execute(
        "SELECT revenue,cost,balance,revenue_progress FROM kpi WHERE project_id=1"
    ).fetchone()
    revenue_by_period = dict(
        conn.execute(
            "SELECT period,SUM(amount) FROM revenue_entry WHERE project_id=1 GROUP BY period"
        ).fetchall()
    )
    cost_by_period = dict(
        conn.execute(
            "SELECT period,SUM(amount) FROM cost_entry WHERE project_id=1 GROUP BY period"
        ).fetchall()
    )
    balance_category_entries = conn.execute(
        "SELECT COUNT(*) FROM cost_entry WHERE project_id=1 AND category LIKE '%بالانس%'"
    ).fetchone()[0]
    work_groups = {
        row["row_no"]: (row["position"], row["zone"])
        for row in conn.execute(
            "SELECT row_no,position,zone FROM activity WHERE project_id=1 AND row_no BETWEEN 4 AND 14"
        )
    }
    invalid_dependencies = conn.execute(
        """
        SELECT COUNT(*)
        FROM activity_dependency d
        JOIN activity child ON child.id=d.activity_id
        JOIN activity parent ON parent.id=d.predecessor_activity_id
        WHERE child.position IS NULL OR child.position='' OR child.zone IS NULL OR child.zone=''
           OR parent.position IS NULL OR parent.position='' OR parent.zone IS NULL OR parent.zone=''
           OR child.position<>parent.position OR child.zone<>parent.zone
        """
    ).fetchone()[0]
    conn.close()

    assert activities >= 10, "At least the major operational activities should be imported"
    assert resources >= 10, "Machine, manpower, and material sheets should be imported into resources"
    assert manpower > 0, "Manpower resource sheet should be imported"
    assert risks == 7, "Every project risk from the workbook should be imported"
    assert physical_entries == physical_entries_expected > 0
    assert physical_mapping_warnings > 0, "Unmatched physical progress must be visible rather than silently discarded"
    assert formula_errors == 1, "Only the one unresolved source formula should remain visible"
    assert len(formula_error_details) == 1 and "T58" in formula_error_details[0]
    assert tuple(first_risk[:5]) == ("مالی", 5, 5, 5, 125)
    assert first_risk["consequence"] and first_risk["existing_controls"] == "--"
    assert tunnel4 >= 1, "Tunnel 4 operational activity should be imported"
    assert revenue_entries == 6, "Revenue line items from both months should be imported"
    assert cost_entries == 12, "All six cost categories from both months should be imported"
    assert balance_category_entries == 0, "Project balance must not be stored as a cost"
    assert dependencies > 0, "Activity dependencies should be inferred from workbook sequencing"
    assert set(monthly) == {"مهر 1405", "آبان 1405"}
    assert work_groups[5] == ("محدوده روباز", "خط غربی")
    assert work_groups[7] == ("تونل شماره 2", "خط شرقی")
    assert work_groups[12] == ("تونل شماره 3", "خط غربی")
    assert invalid_dependencies == 0, "Dependencies must not cross or infer blank work groups"
    assert math.isclose(sum(value[0] for value in monthly.values()), financial_totals["revenue"], abs_tol=0.1)
    assert math.isclose(sum(value[1] for value in monthly.values()), financial_totals["cost"], abs_tol=0.1)
    for period, (revenue, cost) in monthly.items():
        assert math.isclose(revenue_by_period[period], revenue, abs_tol=0.1)
        assert math.isclose(cost_by_period[period], cost, abs_tol=0.1)
    assert math.isclose(financial_totals["balance"], financial_totals["revenue"] - financial_totals["cost"], abs_tol=0.1)
    assert math.isclose(financial_totals["revenue_progress"], 0.14962598867734503, abs_tol=1e-12)
    assert math.isclose(detail_cost, 445_741_374_203.4047, abs_tol=0.1)
    assert 12_300_000_000 < detail_cost - financial_totals["cost"] < 12_400_000_000
    assert risk_score_mismatches == 0
    assert database_integrity == "ok"
    assert not foreign_key_errors
