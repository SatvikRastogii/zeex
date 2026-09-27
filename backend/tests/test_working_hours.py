from datetime import datetime, timedelta

import pytest

from app.domain.working_hours import add_working_hours, in_working_hours, next_working_time
from app.jobs.clock import IST


def ist(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, d, h, m, tzinfo=IST)


@pytest.mark.parametrize(
    ("at", "want"),
    [
        (ist(25, 14), ist(25, 14)),  # inside
        (ist(25, 9), ist(25, 9)),  # opening minute counts
        (ist(25, 7, 30), ist(25, 9)),  # early morning -> same day 09:00
        (ist(25, 20), ist(26, 9)),  # closing minute is closed
        (ist(25, 23, 45), ist(26, 9)),  # night -> next day
    ],
)
def test_next_working_time(at: datetime, want: datetime) -> None:
    assert next_working_time(at) == want


def test_result_keeps_callers_timezone() -> None:
    utc = ist(25, 22).astimezone(IST).astimezone(ist(25, 0).tzinfo)
    assert next_working_time(utc) == ist(26, 9)


@pytest.mark.parametrize(
    ("at", "hours", "want"),
    [
        (ist(25, 10), 3, ist(25, 13)),
        (ist(25, 18), 3, ist(26, 10)),  # 2 h today, 1 h tomorrow
        (ist(25, 21), 3, ist(26, 12)),  # starts counting at next opening
        (ist(25, 9), 11, ist(25, 20)),  # exactly a full day
        (ist(25, 19), 12, ist(26, 20)),  # 1 h today + 11 h tomorrow, lands on closing
    ],
)
def test_add_working_hours(at: datetime, hours: float, want: datetime) -> None:
    assert add_working_hours(at, hours) == want


def test_custom_hours() -> None:
    assert next_working_time(ist(25, 8, 30), "08:00", "18:00") == ist(25, 8, 30)
    assert add_working_hours(ist(25, 17), 2, "08:00", "18:00") == ist(26, 9)


def test_in_working_hours() -> None:
    assert in_working_hours(ist(25, 12))
    assert not in_working_hours(ist(25, 20) + timedelta(minutes=1))


@pytest.mark.parametrize(
    ("at", "want"),
    [
        (ist(25, 14), ist(25, 14)),
        (ist(25, 21), ist(25, 19, 59)),  # evening -> same day's last minute
        (ist(26, 7), ist(25, 19, 59)),  # early morning -> previous evening
    ],
)
def test_last_working_time(at: datetime, want: datetime) -> None:
    from app.domain.working_hours import last_working_time

    assert last_working_time(at) == want


def test_reminder_never_lands_on_the_close() -> None:
    from app.agents.outreach import reminder_time

    hours = {"start": "09:00", "end": "20:00"}
    opens, closes = ist(28, 9), ist(29, 9)  # half-way is 21:00, next opening is the close
    assert reminder_time(ist(28, 21), opens, closes, hours) == ist(28, 19, 59)
    opens2, closes2 = ist(25, 14), ist(26, 14)  # half-way 02:00 -> 09:00 is fine
    assert reminder_time(ist(26, 2), opens2, closes2, hours) == ist(26, 9)
    # a window that sits entirely outside working hours gets no reminder
    assert reminder_time(ist(25, 22), ist(25, 21), ist(25, 23), hours) is None
