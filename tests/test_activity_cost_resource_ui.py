import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.db import connect as db_connect
import app.modules as modules


def test_activity_cost_resource_page_loads_unit_costs_and_shortages(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "activity-cost-ui.db"
    conn = db_connect(db_path)
    conn.execute("INSERT INTO project(id,name) VALUES(1,'UI test')")
    activity_id = conn.execute(
        """
        INSERT INTO activity(
            project_id,row_no,position,zone,title,unit,quantity,remaining_qty,daily_target,source
        ) VALUES(1,1,'تونل ۴','خط شرقی','بتن‌ریزی اسلب','متر',10,10,2,'MANUAL')
        """
    ).lastrowid
    conn.execute(
        """
        INSERT INTO activity_cost_item(
            project_id,activity_id,category,item_name,unit,quantity_per_activity_unit,unit_price,source
        ) VALUES(1,?,'مصالح','بتن','مترمکعب',1.5,100,'MANUAL')
        """,
        (activity_id,),
    )
    resource_id = conn.execute(
        """
        INSERT INTO resource(project_id,category,title,available,unit,unit_price,source)
        VALUES(1,'نیروی انسانی','نیروی بتن‌ریزی',1,'نفر',50,'MANUAL')
        """
    ).lastrowid
    conn.execute(
        """
        INSERT INTO activity_daily_plan(activity_id,plan_date,quantity,source)
        VALUES(?,'2026-10-01',2,'MANUAL')
        """,
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO activity_resource_requirement(
            project_id,activity_id,resource_id,category,resource_title,unit,
            quantity_per_activity_unit,source
        ) VALUES(1,? ,?,'نیروی انسانی','نیروی بتن‌ریزی','نفر',1.5,'MANUAL')
        """,
        (activity_id, resource_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(modules, "connect", lambda: db_connect(db_path))
    page = modules.ActivityCostResourcePage(project_id=1)

    assert page.activity_selector.count() == 1
    assert page.cost_table.rowCount() == 1
    assert page.cost_table.item(0, 5).text() == "150"
    assert page.cost_table.item(0, 6).text() == "1,500"
    assert page.resource_table.rowCount() == 1
    assert page.resource_table.item(0, 6).text() == "3.00"
    assert page.resource_table.item(0, 7).text() == "1.00"
    assert page.resource_table.item(0, 8).text() == "2.00"

    page.close()
    assert app is not None


def test_main_window_module_imports_with_new_page():
    import main

    assert hasattr(main, "MainWindow")
    assert hasattr(modules, "ActivityCostResourcePage")
