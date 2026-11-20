from datetime import date

import pytest

from app.services.schedule_analysis import (
    analyze_critical_path,
    shift_working_days,
    working_finish,
)


def test_only_explicit_holidays_are_excluded_from_project_days():
    # Friday is an ordinary scheduled day unless explicitly registered.
    assert shift_working_days("2026-10-15", 1) == date(2026, 10, 16)
    assert shift_working_days("2026-10-15", 1, holidays=("2026-10-16",)) == date(2026, 10, 17)
    assert working_finish("2026-10-15", 3, holidays=("2026-10-16",)) == date(2026, 10, 18)


def test_project_calendar_validates_duration():
    with pytest.raises(ValueError):
        working_finish("2026-10-15", 0)


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
    assert result["project_duration_days"] == 6
    assert result["critical_activity_ids"] == [1, 2, 3]
    assert by_id[1]["total_float_days"] == 0
    assert by_id[4]["total_float_days"] == 4
    assert by_id[1]["earliest_start"] == date(2026, 10, 15)
    assert by_id[1]["earliest_finish"] == date(2026, 10, 16)


def test_critical_path_supports_finish_to_finish_relationship():
    result = analyze_critical_path(
        [{"id": 1, "duration_days": 4}, {"id": 2, "duration_days": 2}],
        [{"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "FF"}],
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start_day"] == 2
    assert by_id[2]["earliest_finish_day"] == 3


def test_ss_zero_lag_allows_same_start_date():
    result = analyze_critical_path(
        [
            {"id": 1, "duration_days": 3, "start_date": "2026-10-15"},
            {"id": 2, "duration_days": 2, "start_date": "2026-10-15"},
        ],
        [{"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "SS", "lag_days": 0}],
        project_start="2026-10-15",
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start"] == date(2026, 10, 15)


def test_sf_zero_lag_requires_successor_finish_not_before_predecessor_start():
    result = analyze_critical_path(
        [
            {"id": 1, "duration_days": 1, "start_date": "2026-10-15"},
            {"id": 2, "duration_days": 2, "start_date": "2026-10-13"},
        ],
        [{"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "SF", "lag_days": 0}],
        project_start="2026-10-13",
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start"] == date(2026, 10, 14)
    assert by_id[2]["earliest_finish"] == date(2026, 10, 15)


def test_critical_path_detects_cycles():
    result = analyze_critical_path(
        [{"id": 1, "duration_days": 1}, {"id": 2, "duration_days": 1}],
        [
            {"activity_id": 1, "predecessor_activity_id": 2, "relation_type": "FS"},
            {"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "FS"},
        ],
    )
    assert set(result["cycle"]) == {1, 2}
    assert result["project_duration_days"] is None


def test_critical_path_respects_later_baseline_start_dates():
    result = analyze_critical_path(
        [
            {"id": 1, "title": "Early", "duration_days": 1, "start_date": "2026-10-15"},
            {"id": 2, "title": "Later", "duration_days": 1, "start_date": "2026-10-20"},
        ]
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start_day"] == 5
    assert by_id[2]["earliest_start"] == date(2026, 10, 20)
    assert result["project_duration_days"] == 6


def test_critical_path_skips_only_registered_holidays():
    result = analyze_critical_path(
        [{"id": 1, "title": "Pour", "duration_days": 3, "start_date": "2026-10-15"}],
        holidays=("2026-10-16",),
    )
    activity = result["activities"][0]
    assert activity["earliest_start"] == date(2026, 10, 15)
    assert activity["earliest_finish"] == date(2026, 10, 18)
    assert result["project_duration_days"] == 3


def test_activity_start_on_registered_holiday_moves_to_next_project_day():
    result = analyze_critical_path(
        [{"id": 1, "title": "Work", "duration_days": 2, "start_date": "2026-10-16"}],
        holidays=("2026-10-16",),
    )
    activity = result["activities"][0]
    assert activity["earliest_start"] == date(2026, 10, 17)
    assert activity["earliest_finish"] == date(2026, 10, 18)


def test_fs_zero_lag_starts_on_next_non_holiday_day():
    result = analyze_critical_path(
        [
            {"id": 1, "duration_days": 1, "start_date": "2026-10-15"},
            {"id": 2, "duration_days": 2, "start_date": "2026-10-16"},
        ],
        [{"activity_id": 2, "predecessor_activity_id": 1, "relation_type": "FS", "lag_days": 0}],
        holidays=("2026-10-16",),
    )
    by_id = {item["id"]: item for item in result["activities"]}
    assert by_id[2]["earliest_start"] == date(2026, 10, 17)
    assert by_id[2]["earliest_finish"] == date(2026, 10, 18)
