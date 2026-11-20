from datetime import date

import pytest

from app.db import connect
from app.services.schedule_baseline import (
    compare_schedule_to_baseline,
    create_schedule_baseline,
    list_schedule_baselines,
)


def _project_with_activity(tmp_path):
    conn = connect(tmp_path / "baseline.db")
    conn.execute("INSERT INTO project(id,name) VALUES(1,'Baseline test')")
    activity_id = conn.execute(
        """
        INSERT INTO activity(
            project_id,row_no,title,quantity,unit,start_date,finish_date,
            duration_days,daily_target,remaining_qty,source
        ) VALUES(1,1,'Track installation',100,'m','2026-10-15','2026-10-17',3,10,100,'MANUAL')
        """
    ).lastrowid
    conn.commit()
    return conn, activity_id


def test_baseline_is_an_immutable_snapshot_and_reports_finish_variance(tmp_path):
    conn, activity_id = _project_with_activity(tmp_path)
    conn.execute(
        "INSERT INTO calendar_holiday(project_id,holiday_date,title) VALUES(1,'2026-10-16','Project holiday')"
    )
    baseline_id = create_schedule_baseline(conn, 1, "مصوب اولیه", "نسخه مصوب")
    conn.commit()

    conn.execute(
        "UPDATE activity SET finish_date='2026-10-19',duration_days=4 WHERE id=?",
        (activity_id,),
    )
    conn.commit()

    comparison = compare_schedule_to_baseline(conn, baseline_id, as_of=date(2026, 10, 15))
    assert comparison["baseline_name"] == "مصوب اولیه"
    assert comparison["baseline_project_finish"] == date(2026, 10, 17)
    assert comparison["forecast_project_finish"] == date(2026, 10, 19)
    assert comparison["project_finish_variance_calendar_days"] == 2
    assert comparison["activities"][0]["baseline_duration_days"] == 2
    assert comparison["activities"][0]["finish_variance_calendar_days"] == 2

    # The live calendar can change without rewriting the historical baseline calendar.
    conn.execute(
        "DELETE FROM calendar_holiday WHERE project_id=1 AND holiday_date='2026-10-16'"
    )
    conn.commit()
    saved_holiday = conn.execute(
        "SELECT holiday_date FROM schedule_baseline_holiday WHERE baseline_id=?",
        (baseline_id,),
    ).fetchone()[0]
    assert saved_holiday == "2026-10-16"
    assert len(list_schedule_baselines(conn, 1)) == 1
    conn.close()


def test_baseline_rejects_activities_without_planned_dates(tmp_path):
    conn, _activity_id = _project_with_activity(tmp_path)
    conn.execute("INSERT INTO activity(project_id,row_no,title) VALUES(1,2,'Undated')")
    conn.commit()

    with pytest.raises(ValueError, match="planned start and finish"):
        create_schedule_baseline(conn, 1, "Incomplete")
    assert conn.execute("SELECT COUNT(*) FROM schedule_baseline").fetchone()[0] == 0
    conn.close()


def test_baseline_requires_a_nonempty_name(tmp_path):
    conn, _activity_id = _project_with_activity(tmp_path)
    with pytest.raises(ValueError, match="name is required"):
        create_schedule_baseline(conn, 1, "  ")
    conn.close()
