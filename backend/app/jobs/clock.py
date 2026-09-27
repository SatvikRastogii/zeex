"""All code reads time through a Clock so the demo can fast-forward.

Timestamps are UTC. Business rules (working hours, deadlines, ID years) use IST.
"""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Protocol

# IST is a fixed +05:30 with no DST, so no tz database (Windows has none) is needed.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class DemoClock:
    """Real time shifted by an offset that only moves forward."""

    def __init__(self, offset: timedelta = timedelta(0)) -> None:
        self.offset = offset

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("demo clock cannot go backwards")
        self.offset += delta


class FixedClock:
    """Frozen time for tests."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs an aware datetime")
        self.at = at

    def now(self) -> datetime:
        return self.at

    def advance(self, delta: timedelta) -> None:
        self.at += delta


def to_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST)


def ist_today(clock: Clock) -> date:
    return to_ist(clock.now()).date()


def ist_year(clock: Clock) -> int:
    return to_ist(clock.now()).year


def ist_week_start(d: date) -> date:
    """Monday of d's week (capacity is tracked per IST week)."""
    return d - timedelta(days=d.weekday())


def format_ist(dt: datetime) -> str:
    """'25 Sep 2026 14:05 IST'"""
    return to_ist(dt).strftime("%d %b %Y %H:%M IST")


def ist_datetime(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST).astimezone(UTC)
