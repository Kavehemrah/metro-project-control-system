from datetime import date

import pytest

from app.services.schedule_analysis import (
    analyze_critical_path,
    shift_working_days,
    working_finish,
)


def test_working_calendar_skips_holiday_and_friday():
    # 2026-10-15 is Thursday; Saturday 17th is a holiday.
    assert shift_working_days("2026-10-15", 1, holidays=("2026-10-17",)) == date(2026, 10, 18)
    assert working_finish("2026-10-15", 3, holidays=("2026-10-17",)) == date(2026, 10, 19)


def test_working_calendar_validates_duration_and_weekdays():
    with pytest.raises(ValueError):
        working_finish("2026-10-15", 0)
    with pytest.raises(ValueError):
        shift_working_days("2026-10-15", 1, working_weekdays=())


def test_critical_path_marks_zero_float_chain_and_parallel_float():
    activities = [
        {"id": 1, "title": "A", "duration_days": 2},
        {"id": 2, "title": "B", "duration_days": 3},
        {"id": 3, "title": "C", "duration_days": 1},
        {"id": 4, "title": "Short parallel", "duration_days": 1},
    ]
    dependencies = [
        {"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "FS"},
        {"activity_id": 3, "predecessor_activity_id": 2, "relation_type": "FS"},
        {"activity_id": 3, "predecessor_activity_id": 4, "relation_type": "FS"},
    ]
    result = analyze_critical_path(activities, dependencies, project_start="2026-10-15")
    by_id = {item["id"]: item for item in result["activities"]}
    assert result["cycle"] == []
    assert result["project_duration_workdays"] == 6
    assert result["critical_activity_ids"] == [1, 2, 3]
    assert by_id[1]["total_float_workdays"] == 0
    assert by_id[4]["total_float_workdays"] == 4
    assert by_id[1]["earliest_start"] == date(2026, 10, 15)
    assert by_id[1]["earliest_finish"] == date(2026, 10, 17)


def test_critical_path_supports_finish_to_finish_relationship():
    result = analyze_critical_path(
        [{"id": 1, "duration_days": 4}, {"id": 2, "duration_days": 2}],
        [{"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "FF"}],
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start_workday"] == 2
    assert by_id[2]["earliest_finish_workday"] == 3


def test_critical_path_detects_cycles():
    result = analyze_critical_path(
        [{"id": 1, "duration_days": 1}, {"id": 2, "duration_days": 1}],
        [
            {"activity_id": 1, "predecessor_activity_id": 2, "relation_type": "FS"},
            {"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "FS"},
        ],
    )
    assert set(result["cycle"]) == {1, 2}
    assert result["project_duration_workdays"] is None

def test_critical_path_respects_later_baseline_start_dates():
    result = analyze_critical_path(
        [
            {"id": 1, "title": "Early", "duration_days": 1, "start_date": "2026-10-15"},
            {"id": 2, "title": "Later", "duration_days": 1, "start_date": "2026-10-20"},
        ]
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start_workday"] == 4
    assert by_id[2]["earliest_start"] == date(2026, 10, 20)
    assert result["project_duration_workdays"] == 5


def test_critical_path_skips_registered_holidays():
    result = analyze_critical_path(
        [{"id": 1, "title": "Pour", "duration_days": 3, "start_date": "2026-10-15"}],
        holidays=("2026-10-17",),
    )
    activity = result["activities"][0]
    assert activity["earliest_start"] == date(2026, 10, 15)
    assert activity["earliest_finish"] == date(2026, 10, 19)
    assert result["project_duration_workdays"] == 3


def test_critical_path_holiday_can_extend_activity_start_and_finish():
    result = analyze_critical_path(
        [{"id": 1, "title": "Work", "duration_days": 2, "start_date": "2026-10-17"}],
        holidays=("2026-10-17",),
    )
    activity = result["activities"][0]
    assert activity["earliest_start"] == date(2026, 10, 18)
    assert activity["earliest_finish"] == date(2026, 10, 19)
