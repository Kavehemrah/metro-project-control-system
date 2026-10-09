import sqlite3
from pathlib import Path

from openpyxl import Workbook

from app.db import connect
from app.services.control import activity_control, critical_activities, scan_formula_errors, update_activity_rollup


def test_critical_activities_prioritize_status_and_delay():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE activity (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            row_no INTEGER,
            title TEXT,
            zone TEXT,
            status TEXT,
            delay_days INTEGER,
            quantity REAL,
            plan_remaining_qty REAL,
            remaining_qty REAL,
            baseline_actual_qty REAL,
            actual_qty REAL DEFAULT 0,
            planned_qty REAL DEFAULT 0,
            progress REAL,
            daily_target REAL,
            start_date TEXT,
            finish_date TEXT,
            forecast_finish TEXT
        );
        CREATE TABLE activity_daily_plan (
            activity_id INTEGER, plan_date TEXT, quantity REAL, source TEXT DEFAULT 'EXCEL'
        );
        CREATE TABLE activity_daily_actual (
            activity_id INTEGER, actual_date TEXT, quantity REAL, source TEXT DEFAULT 'MANUAL', notes TEXT
        );
        CREATE TABLE activity_period (
            activity_id INTEGER, period TEXT, planned_qty REAL, actual_qty REAL, source TEXT DEFAULT 'EXCEL'
        );
        INSERT INTO activity VALUES
            (1, 1, 1, 'عادی', 'جبهه ۱', 'NORMAL', 0, 100, 0, 40, 0, 0, 0, 0.6, 10, NULL, NULL, NULL),
            (2, 1, 2, 'هشدار', 'جبهه ۱', 'WARNING', 4, 100, 0, 60, 0, 0, 0, 0.4, 10, NULL, NULL, NULL),
            (3, 1, 3, 'بحرانی', 'جبهه ۲', 'CRITICAL', 1, 100, 0, 50, 20, 20, 0, 0.5, 10, NULL, NULL, NULL);
        """
    )

    result = critical_activities(conn)
    conn.execute(
        "INSERT INTO activity_daily_plan(activity_id,plan_date,quantity,source) VALUES(3,'2026-10-01',30,'MANUAL')"
    )
    conn.execute(
        "INSERT INTO activity_daily_actual(activity_id,actual_date,quantity,source) VALUES(3,'2026-10-01',7,'MANUAL')"
    )
    updated_control = update_activity_rollup(conn, 3)
    updated_status = conn.execute("SELECT status FROM activity WHERE id=3").fetchone()[0]

    assert [item["title"] for item in result] == ["بحرانی", "هشدار"]
    assert result[0]["priority_label"] == "بحرانی"
    assert result[0]["remaining_qty"] == 80
    assert result[0]["physical_progress_pct"] == 20
    assert result[1]["reason"] == "تأخیر 4 روز، وضعیت WARNING"
    assert updated_control["actual_qty"] == 27
    assert updated_control["remaining_qty"] == 73
    assert updated_control["physical_progress_pct"] == 27
    # Period achievement uses the 7 units recorded during the plan window,
    # not the 20-unit cumulative baseline plus those 7 units.
    assert updated_control["period_actual_qty"] == 7
    assert updated_control["achievement_pct"] < 90
    assert updated_status == "CRITICAL"
    conn.close()


def test_formula_scanner_reports_missing_cached_results():
    formulas = Workbook()
    formulas.active["A1"] = "=1+1"
    cached = Workbook()

    result = scan_formula_errors(formulas, cached)

    assert len(result) == 1
    assert result[0]["title"] == "فرمول Excel بدون مقدار محاسبه‌شده"
    assert "A1" in result[0]["detail"]


def test_legacy_activity_migration_separates_manual_actual_from_baseline(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(database_path)
    legacy.executescript(
        """
        CREATE TABLE project (id INTEGER PRIMARY KEY);
        INSERT INTO project(id) VALUES(1);
        CREATE TABLE activity (
            id INTEGER PRIMARY KEY, project_id INTEGER, row_no INTEGER,
            position TEXT, zone TEXT, title TEXT, quantity REAL,
            remaining_qty REAL, unit TEXT, start_date TEXT, finish_date TEXT,
            duration_days INTEGER, daily_target REAL, planned_qty REAL,
            actual_qty REAL, progress REAL, delay_days INTEGER, status TEXT
        );
        CREATE TABLE activity_daily_actual (
            id INTEGER PRIMARY KEY, activity_id INTEGER, actual_date TEXT,
            quantity REAL, source TEXT, notes TEXT
        );
        INSERT INTO activity VALUES
            (1,1,13,'Tunnel 4','East','Rail transfer',100,80,'m',NULL,NULL,10,10,0,25,0.25,0,'NORMAL');
        INSERT INTO activity_daily_actual(activity_id,actual_date,quantity,source)
            VALUES(1,'2026-10-09',5,'MANUAL');
        """
    )
    legacy.commit()
    legacy.close()

    conn = connect(database_path)
    row = conn.execute(
        "SELECT plan_remaining_qty,remaining_qty,baseline_actual_qty,actual_qty "
        "FROM activity WHERE id=1"
    ).fetchone()
    control = activity_control(conn, 1)

    assert tuple(row) == (80, 75, 20, 25)
    assert control["actual_qty"] == 25
    assert control["remaining_qty"] == 75
    conn.close()