import sqlite3

from app.services.control import critical_activities


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
            remaining_qty REAL,
            progress REAL,
            daily_target REAL,
            start_date TEXT,
            finish_date TEXT
        );
        CREATE TABLE activity_daily_plan (activity_id INTEGER, quantity REAL);
        CREATE TABLE activity_daily_actual (activity_id INTEGER, quantity REAL);
        INSERT INTO activity VALUES
            (1, 1, 1, 'عادی', 'جبهه ۱', 'NORMAL', 0, 100, 40, 0.6, 10, NULL, NULL),
            (2, 1, 2, 'هشدار', 'جبهه ۱', 'WARNING', 4, 100, 60, 0.4, 10, NULL, NULL),
            (3, 1, 3, 'بحرانی', 'جبهه ۲', 'CRITICAL', 1, 100, 50, 0.5, 10, NULL, NULL);
        """
    )

    result = critical_activities(conn)

    assert [item["title"] for item in result] == ["بحرانی", "هشدار"]
    assert result[0]["priority_label"] == "بحرانی"
    assert result[0]["remaining_qty"] == 50
    assert result[0]["physical_progress_pct"] == 50
    assert result[1]["reason"] == "تأخیر 4 روز، وضعیت WARNING"
    conn.close()