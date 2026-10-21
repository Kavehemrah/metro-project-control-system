import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from app.db import connect as db_connect
import app.modules as modules
import app.ui as ui


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
        line_edits = dialog.findChildren(QLineEdit)
        line_edits[-1].setText("تست ورود منبع")
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



def test_finance_page_keeps_long_summaries_inside_task_tabs(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "finance-tabs.db"
    conn = db_connect(db_path)
    conn.execute(
        "INSERT INTO project(id,name,contract_value,period_label) VALUES(1,'Finance UI test',100000,'مهر 1405')"
    )
    conn.execute(
        "INSERT INTO kpi(project_id,physical_progress,revenue,cost,balance,revenue_progress) "
        "VALUES(1,0,1000,600,400,0)"
    )
    conn.execute(
        "INSERT INTO monthly_finance(project_id,month,revenue,cost,source) "
        "VALUES(1,'مهر 1405',1000,600,'EXCEL')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(modules, "connect", lambda: db_connect(db_path))
    page = modules.FinancePage()

    assert page.finance_tabs.count() == 4
    assert [page.finance_tabs.tabText(index) for index in range(4)] == [
        "پوشش هزینه فعالیت‌ها", "دفتر مالی واقعی", "خلاصه دوره‌ای", "تخصیص به فعالیت"
    ]
    assert page.coverage_summary.parentWidget() is page.finance_tabs.widget(0)
    assert page.actual_summary.parentWidget() is page.finance_tabs.widget(1)
    assert page.forecast_summary.parentWidget() is page.finance_tabs.widget(2)

    page.close()
    assert app is not None


def test_dashboard_shows_reconciliation_detail_and_prioritizes_high_discrepancies(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "dashboard-discrepancies.db"
    conn = db_connect(db_path)
    conn.execute(
        "INSERT INTO project(id,name,contract_value,period_label) "
        "VALUES(1,'Dashboard test',100000,'مهر 1405')"
    )
    conn.execute(
        "INSERT INTO kpi(project_id,physical_progress,revenue,cost,balance,revenue_progress) "
        "VALUES(1,0.1426,679000,433000,246000,0.15)"
    )
    conn.execute(
        "INSERT INTO discrepancy(project_id,title,detail,severity,source) "
        "VALUES(1,?,?,?,'EXCEL')",
        (
            "مغایرت هزینه خلاصه و جزئیات",
            "اختلاف 12,326,556,667 ریال بین خلاصه و ریز هزینه‌ها.",
            "HIGH",
        ),
    )
    conn.execute(
        "INSERT INTO discrepancy(project_id,title,detail,severity,source) "
        "VALUES(1,?,?,?,'EXCEL')",
        ("مورد تطبیق کم‌اهمیت‌تر", "شرح آزمایشی", "MEDIUM"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(ui, "connect", lambda: db_connect(db_path))
    page = ui.Dashboard()
    labels = [label.text() for label in page.findChildren(QLabel)]
    detailed_label = next(
        label for label in labels
        if "اختلاف 12,326,556,667 ریال" in label
    )
    assert "مغایرت هزینه خلاصه و جزئیات" in labels[labels.index(detailed_label) - 1]
    assert "[HIGH]" in labels[labels.index(detailed_label) - 1]

    visible_discrepancy_titles = [
        label for label in labels
        if label.startswith("• [") and ("مغایرت هزینه" in label or "کم‌اهمیت‌تر" in label)
    ]
    assert visible_discrepancy_titles[0].startswith("• [HIGH]")
    assert visible_discrepancy_titles[1].startswith("• [MEDIUM]")

    page.close()
    assert app is not None
