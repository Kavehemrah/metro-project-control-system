from pathlib import Path

from app.db import connect
from imports.import_excel import import_workbook


def test_workbook_import_includes_resource_sheets():
    workbook = Path(__file__).resolve().parents[1] / "metro-project-control-system.xlsx"
    assert workbook.exists(), "Workbook should exist in project root"

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
    manpower = conn.execute("SELECT COUNT(*) FROM resource WHERE project_id=1 AND category='نیروی انسانی'").fetchone()[0]
    tunnel4 = conn.execute(
        "SELECT COUNT(*) FROM activity WHERE project_id=1 AND (position LIKE '%تونل%' OR zone LIKE '%تونل%' OR title LIKE '%تونل%') AND (position LIKE '%4%' OR zone LIKE '%4%' OR title LIKE '%4%')"
    ).fetchone()[0]
    revenue_entries = conn.execute("SELECT COUNT(*) FROM revenue_entry WHERE project_id=1").fetchone()[0]
    cost_entries = conn.execute("SELECT COUNT(*) FROM cost_entry WHERE project_id=1").fetchone()[0]
    dependencies = conn.execute("SELECT COUNT(*) FROM activity_dependency WHERE project_id=1").fetchone()[0]
    conn.close()

    assert activities >= 10, "At least the major operational activities should be imported"
    assert resources >= 10, "Machine, manpower, and material sheets should be imported into resources"
    assert manpower > 0, "Manpower resource sheet should be imported"
    assert tunnel4 >= 1, "Tunnel 4 operational activity should be imported"
    assert revenue_entries > 0, "Revenue detail rows should be imported from the workbook"
    assert cost_entries > 0, "Cost detail rows should be imported from the workbook"
    assert dependencies > 0, "Activity dependencies should be inferred from workbook sequencing"
