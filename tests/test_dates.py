from datetime import date

import pytest

import jdatetime

from app.services.dates import format_date, normalize_date


def test_normalize_jalali_date_to_gregorian_iso():
    assert normalize_date("1405/07/15") == "2026-10-07"
    assert normalize_date("۱۴۰۵/۰۷/۱۵") == "2026-10-07"
    assert normalize_date("1405-07-15") == "2026-10-07"


def test_normalize_gregorian_date_and_date_object():
    assert normalize_date("2026-10-07") == "2026-10-07"
    assert normalize_date(date(2026, 10, 7)) == "2026-10-07"


@pytest.mark.parametrize("value", ["", None, "1405/13/01", "1405/07/32", "not-a-date"])
def test_reject_invalid_dates(value):
    with pytest.raises(ValueError):
        normalize_date(value)


def test_normalize_date_accepts_timestamp_in_either_order():
    assert normalize_date("2026-10-09 00:00:00") == "2026-10-09"
    assert normalize_date("00:00:00 2026-10-09") == "2026-10-09"
    assert normalize_date("1405/07/17 00:00:00") == jdatetime.date(1405, 7, 17).togregorian().isoformat()


def test_format_date_renders_gregorian_storage_as_jalali():
    expected = jdatetime.date.fromgregorian(date=date(2026, 10, 9))
    rendered = f"{expected.year:04d}/{expected.month:02d}/{expected.day:02d}"
    assert format_date("2026-10-09") == rendered
    assert format_date("2026-10-09 00:00:00") == rendered
    assert format_date("1405/07/17 00:00:00") == "1405/07/17"


def test_format_date_returns_empty_string_for_empty_value():
    assert format_date(None) == ""
    assert format_date("") == ""
