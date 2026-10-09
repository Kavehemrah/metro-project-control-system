from datetime import date

import pytest

from app.services.dates import normalize_date


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
