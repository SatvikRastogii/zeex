from datetime import UTC, date, datetime, timedelta

import pytest

from app.jobs.clock import DemoClock, FixedClock, format_ist, ist_week_start, ist_year


def test_ist_year_rolls_over_before_utc() -> None:
    # 31 Dec 2026 19:00 UTC is already 1 Jan 2027 00:30 IST
    assert ist_year(FixedClock(datetime(2026, 12, 31, 19, 0, tzinfo=UTC))) == 2027


def test_format_ist() -> None:
    assert format_ist(datetime(2026, 9, 25, 8, 35, tzinfo=UTC)) == "25 Sep 2026 14:05 IST"


def test_demo_clock_advances_and_never_goes_back() -> None:
    c = DemoClock()
    before = c.now()
    c.advance(timedelta(hours=1))
    assert c.now() - before >= timedelta(hours=1)
    with pytest.raises(ValueError):
        c.advance(timedelta(minutes=-1))


def test_week_start_is_monday() -> None:
    assert ist_week_start(date(2026, 9, 27)) == date(2026, 9, 21)  # Sunday -> Monday
    assert ist_week_start(date(2026, 9, 21)) == date(2026, 9, 21)
