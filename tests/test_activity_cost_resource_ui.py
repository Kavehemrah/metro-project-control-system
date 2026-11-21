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



def test_activity_record_dialog_groups_fields_into_clear_sections():
    app = QApplication.instance() or QApplication([])
    dialog = modules.RecordDialog(
        "افزودن فعالیت",
        modules.ACTIVITY_FIELDS,
    )

    from PySide6.QtWidgets import QGroupBox, QLineEdit

    sections = [group.title() for group in dialog.findChildren(QGroupBox)]
    assert sections == [
        "۱. مشخصات و محل اجرا",
        "۲. مقادیر و واحد",
        "۳. زمان‌بندی",
    ]
    assert set(dialog.inputs) == {
        "position", "zone", "title", "quantity", "unit",
        "start_date", "finish_date", "duration_days", "daily_target",
    }
    assert isinstance(dialog.inputs["start_date"][0], QLineEdit)
    assert "1405/07/17" in dialog.inputs["start_date"][0].toolTip()

    dialog.close()
    assert app is not None



def test_activity_workspace_shows_dated_histories_in_separate_tabs(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "activity-workspace.db"
    conn = db_connect(db_path)
    conn.execute(
        "INSERT INTO project(id,name,contract_value) VALUES(1,'Workspace test',100000)"
    )
    activity_id = conn.execute(
        """
        INSERT INTO activity(
            project_id,row_no,position,zone,title,unit,quantity,remaining_qty,
            start_date,finish_date,source
        ) VALUES(1,7,'تونل ۴','خط شرقی','بتن‌ریزی اسلب','مترمکعب',100,100,
                 '2026-10-01','2026-10-10','MANUAL')
        """
    ).lastrowid
    conn.execute(
        "INSERT INTO activity_daily_plan(activity_id,plan_date,quantity,source) "
        "VALUES(?,'2026-10-02',10,'MANUAL')",
        (activity_id,),
    )
    conn.execute(
        "INSERT INTO activity_daily_actual(activity_id,actual_date,quantity,source,notes) "
        "VALUES(?,'2026-10-03',8,'MANUAL','بتن‌ریزی جبهه شرقی')",
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO activity_period(
            activity_id,period,planned_qty,actual_qty,cumulative_qty,remaining_qty,achievement_pct,source
        ) VALUES(?,'مهر 1405',10,12,12,88,120,'EXCEL')
        """,
        (activity_id,),
    )
    conn.execute(
        "INSERT INTO cost_entry(project_id,activity_id,period,category,amount,source) "
        "VALUES(1,?,'مهر 1405','بودجه هزینه',5000,'MANUAL')",
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO actual_finance_entry(
            project_id,activity_id,entry_date,period,entry_type,category,amount,source
        ) VALUES(1,?,'2026-10-04','مهر 1405','COST','هزینه حمل',700,'MANUAL')
        """,
        (activity_id,),
    )
    conn.execute(
        "INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source) "
        "VALUES(1,?,'مهر 1405','درآمد مبنا',12000,'MANUAL')",
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO actual_finance_entry(
            project_id,activity_id,entry_date,period,entry_type,category,amount,source
        ) VALUES(1,?,'2026-10-05','مهر 1405','REVENUE','درآمد ثبت‌شده',900,'MANUAL')
        """,
        (activity_id,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(modules, "connect", lambda: db_connect(db_path))
    dialog = modules.ActivityWorkspaceDialog(activity_id, project_id=1)

    assert dialog.tabs.count() == 3
    assert [dialog.tabs.tabText(i) for i in range(3)] == [
        "عملکرد روزانه", "هزینه", "درآمد"
    ]
    assert "برنامه اجرایی" not in dialog.tab_specs
    daily_table = dialog.tab_specs["عملکرد روزانه"]["table"]
    cost_table = dialog.tab_specs["هزینه"]["table"]
    revenue_table = dialog.tab_specs["درآمد"]["table"]
    assert daily_table.rowCount() == 2
    daily_sources = [daily_table.item(row, 3).text() for row in range(daily_table.rowCount())]
    assert "Excel - عملکرد دوره‌ای" in daily_sources
    assert "MANUAL" in daily_sources
    assert cost_table.rowCount() == 2
    cost_sources = [cost_table.item(row, 3).text() for row in range(cost_table.rowCount())]
    assert any("مبنای هزینه MANUAL" in value for value in cost_sources)
    assert any("واقعی MANUAL" in value for value in cost_sources)
    assert "مبلغ مبنای هزینه: 5,000" in dialog.tab_specs["هزینه"]["summary"].text()
    assert "هزینه واقعی ثبت‌شده: 700" in dialog.tab_specs["هزینه"]["summary"].text()
    assert revenue_table.rowCount() == 1
    assert revenue_table.item(0, 0).text() == "1405/07/11"
    assert revenue_table.item(0, 1).text() == "960"
    assert revenue_table.item(0, 2).text() == "960"
    assert daily_table.item(
        next(row for row in range(daily_table.rowCount()) if daily_table.item(row, 3).text() == "MANUAL"),
        4,
    ).text() == "960"
    assert "درآمد کل بازه انتخاب‌شده: 960" in dialog.tab_specs["درآمد"]["summary"].text()
    assert revenue_table.isEnabled()
    filters = dialog.tab_specs["درآمد"]["filters"]
    filters["date_from"].setText("1405/07/12")
    dialog._apply_revenue_filter("درآمد")
    assert revenue_table.rowCount() == 0
    dialog._clear_revenue_filter("درآمد")
    assert revenue_table.rowCount() == 1

    dialog.close()
    assert app is not None



def test_activity_row_click_opens_correct_workspace_and_shows_daily_finance_totals(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "activity-row-click.db"
    conn = db_connect(db_path)
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Row click test')")
    activity_id = conn.execute(
        """
        INSERT INTO activity(
            project_id,row_no,position,zone,title,unit,quantity,remaining_qty,source
        ) VALUES(1,1,'تونل ۴','خط شرقی','بتن‌ریزی اسلب','مترمکعب',100,100,'MANUAL')
        """
    ).lastrowid
    conn.execute(
        """
        INSERT INTO activity_daily_actual(activity_id,actual_date,quantity,source,notes)
        VALUES(?,'2026-10-03',8,'MANUAL','عملکرد آزمایشی')
        """,
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO activity_period(
            activity_id,period,planned_qty,actual_qty,cumulative_qty,remaining_qty,achievement_pct,source
        ) VALUES(?,'مهر 1405',10,12,12,88,120,'EXCEL')
        """,
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source)
        VALUES(1,?,'کل پروژه','درآمد فعالیت',12000,'EXCEL_ACTIVITY')
        """,
        (activity_id,),
    )
    conn.execute(
        """
        INSERT INTO actual_finance_entry(
            project_id,activity_id,entry_date,entry_type,category,amount,source
        ) VALUES(1,?,'2026-10-04','COST','هزینه حمل',700,'MANUAL')
        """,
        (activity_id,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(modules, "connect", lambda: db_connect(db_path))
    opened = {}

    class FakeWorkspace:
        def __init__(self, selected_activity_id, project_id, parent):
            opened["activity_id"] = selected_activity_id
            opened["project_id"] = project_id

        def exec(self):
            return 0

    monkeypatch.setattr(modules, "ActivityWorkspaceDialog", FakeWorkspace)
    page = modules.ActivityPage("عملکرد روزانه", progress_only=True)

    assert page.table.item(0, 0).data(modules.Qt.UserRole) == activity_id
    assert page.table.item(0, 15).text() == "1405/07/11"
    assert page.table.item(0, 16).text() == "700"
    assert page.table.item(0, 17).text() == "960"

    page.table.cellClicked.emit(0, 0)
    assert opened == {"activity_id": activity_id, "project_id": 1}

    page.close()
    assert app is not None


def test_activity_workspace_allows_editing_and_deleting_excel_history(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog, QMessageBox

    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "activity-workspace-excel-edit.db"
    conn = db_connect(db_path)
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Excel edit test')")
    activity_id = conn.execute(
        """
        INSERT INTO activity(project_id,row_no,position,zone,title,unit,quantity,source)
        VALUES(1,1,'تونل ۴','خط شرقی','بتن‌ریزی اسلب','مترمکعب',100,'EXCEL')
        """
    ).lastrowid
    period_id = conn.execute(
        """
        INSERT INTO activity_period(
            activity_id,period,planned_qty,actual_qty,cumulative_qty,remaining_qty,achievement_pct,source
        ) VALUES(?,'مهر 1405',10,8,8,92,80,'EXCEL')
        """,
        (activity_id,),
    ).lastrowid
    cost_id = conn.execute(
        """
        INSERT INTO cost_entry(project_id,activity_id,period,category,amount,source)
        VALUES(1,?,'مهر 1405','هزینه حمل',5000,'EXCEL')
        """,
        (activity_id,),
    ).lastrowid
    daily_excel_id = conn.execute(
        """
        INSERT INTO activity_daily_actual(activity_id,actual_date,quantity,source,notes)
        VALUES(?,'2026-10-04',6,'EXCEL','عملکرد روزانه واردشده از Excel')
        """,
        (activity_id,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO revenue_entry(project_id,activity_id,period,category,amount,source)
        VALUES(1,?,'کل پروژه','درآمد مبنا',10000,'EXCEL_ACTIVITY')
        """,
        (activity_id,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(modules, "connect", lambda: db_connect(db_path))
    dialog = modules.ActivityWorkspaceDialog(activity_id, project_id=1)

    class AcceptedRecordDialog:
        def __init__(self, title, fields, initial=None, parent=None):
            self.title = title
            self.initial = initial or {}

        def exec(self):
            return QDialog.Accepted

        def values(self):
            if "planned_qty" in self.initial:
                return {"planned_qty": 14, "actual_qty": 13}
            if "actual_date" in self.initial:
                return {
                    "actual_date": "2026-10-06",
                    "quantity": 9,
                    "notes": "عملکرد روزانه اصلاح‌شده",
                }
            return {"period": "آبان 1405", "category": "هزینه اصلاح‌شده", "amount": 6500}

    monkeypatch.setattr(modules, "RecordDialog", AcceptedRecordDialog)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    daily_spec = dialog.tab_specs["عملکرد روزانه"]
    daily_table = daily_spec["table"]
    assert daily_table.rowCount() == 2
    period_row = next(
        row for row in range(daily_table.rowCount())
        if daily_table.item(row, 0).data(modules.Qt.UserRole + 1) == "EXCEL_PERIOD"
    )
    daily_table.selectRow(period_row)
    dialog._edit_daily(daily_spec)

    conn = db_connect(db_path)
    edited_period = conn.execute(
        "SELECT planned_qty,actual_qty FROM activity_period WHERE id=?", (period_id,)
    ).fetchone()
    conn.close()
    assert tuple(edited_period) == (14, 13)

    excel_daily_row = next(
        row for row in range(daily_table.rowCount())
        if daily_table.item(row, 0).data(modules.Qt.UserRole + 1) == "EXCEL"
    )
    daily_table.selectRow(excel_daily_row)
    dialog._edit_daily(daily_spec)

    conn = db_connect(db_path)
    edited_daily = conn.execute(
        "SELECT actual_date,quantity,source,notes FROM activity_daily_actual WHERE id=?",
        (daily_excel_id,),
    ).fetchone()
    conn.close()
    assert tuple(edited_daily) == (
        "2026-10-06", 9, "EXCEL", "عملکرد روزانه اصلاح‌شده"
    )
    revenue_table = dialog.tab_specs["درآمد"]["table"]
    assert revenue_table.rowCount() == 1
    assert revenue_table.item(0, 1).text() == "900"

    cost_spec = dialog.tab_specs["هزینه"]
    cost_table = cost_spec["table"]
    excel_row = next(
        row for row in range(cost_table.rowCount())
        if cost_table.item(row, 0).data(modules.Qt.UserRole + 2) == "cost_entry"
    )
    cost_table.selectRow(excel_row)
    assert cost_table.item(excel_row, 0).data(modules.Qt.UserRole + 1) == "EXCEL"
    dialog._edit_finance(cost_spec)

    conn = db_connect(db_path)
    edited_cost = conn.execute(
        "SELECT period,category,amount FROM cost_entry WHERE id=?", (cost_id,)
    ).fetchone()
    conn.close()
    assert tuple(edited_cost) == ("آبان 1405", "هزینه اصلاح‌شده", 6500)

    period_row = next(
        row for row in range(daily_table.rowCount())
        if daily_table.item(row, 0).data(modules.Qt.UserRole + 1) == "EXCEL_PERIOD"
    )
    daily_table.selectRow(period_row)
    dialog._delete_daily(daily_spec)
    excel_daily_row = next(
        row for row in range(daily_table.rowCount())
        if daily_table.item(row, 0).data(modules.Qt.UserRole + 1) == "EXCEL"
    )
    daily_table.selectRow(excel_daily_row)
    dialog._delete_daily(daily_spec)
    cost_table.selectRow(0)
    dialog._delete_finance(cost_spec)

    conn = db_connect(db_path)
    assert conn.execute("SELECT 1 FROM activity_period WHERE id=?", (period_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM activity_daily_actual WHERE id=?", (daily_excel_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM cost_entry WHERE id=?", (cost_id,)).fetchone() is None
    conn.close()
    assert revenue_table.rowCount() == 0
    assert "درآمد کل بازه انتخاب‌شده: 0" in dialog.tab_specs["درآمد"]["summary"].text()
    dialog.close()
    assert app is not None
