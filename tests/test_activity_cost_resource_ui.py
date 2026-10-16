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



def test_resource_requirement_dialog_filters_source_resources_by_category(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QComboBox, QDialog, QDoubleSpinBox, QLineEdit

    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "resource-category-dialog.db"
    conn = db_connect(db_path)
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Dialog test')")
    activity_id = conn.execute(
        """
        INSERT INTO activity(project_id,row_no,position,zone,title,unit,quantity,source)
        VALUES(1,1,'تونل ۴','خط شرقی','بتن‌ریزی اسلب','متر',10,'MANUAL')
        """
    ).lastrowid
    machine_id = conn.execute(
        """
        INSERT INTO resource(project_id,category,title,unit,source)
        VALUES(1,'ماشین‌آلات','لودر','دستگاه','MANUAL')
        """
    ).lastrowid
    conn.execute(
        """
        INSERT INTO resource(project_id,category,title,unit,source)
        VALUES(1,'نیروی انسانی','تیم بتن‌ریزی','نفر','MANUAL')
        """
    )
    conn.execute(
        """
        INSERT INTO resource(project_id,category,title,unit,source)
        VALUES(1,'مصالح','سیمان','تن','MANUAL')
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(modules, "connect", lambda: db_connect(db_path))
    page = modules.ActivityCostResourcePage(project_id=1)

    def accept_dialog(dialog):
        combos = dialog.findChildren(QComboBox)
        category_selector, resource_selector = combos[0], combos[1]
        category_selector.setCurrentIndex(category_selector.findData("ماشین‌آلات"))
        visible_ids = [
            resource_selector.itemData(index)
            for index in range(resource_selector.count())
            if resource_selector.itemData(index) is not None
        ]
        assert visible_ids == [machine_id]
        resource_selector.setCurrentIndex(resource_selector.findData(machine_id))
        dialog.findChild(QDoubleSpinBox).setValue(1.75)
        dialog.findChild(QLineEdit).setText("تست ورود منبع")
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_dialog)
    result = page._resource_dialog()
    assert result is not None
    values, activity = result
    assert activity["id"] == activity_id
    assert values["resource_id"] == machine_id
    assert values["category"] == "ماشین‌آلات"
    assert values["resource_title"] == "لودر"
    assert values["quantity_per_activity_unit"] == 1.75
    assert values["notes"] == "تست ورود منبع"

    page.close()
    assert app is not None
