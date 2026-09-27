"""Working-hours arithmetic in IST (default 09:00-20:00, every day).

Messages to vendors are only sent inside the window; timeouts such as "3 working
hours" only count time inside it. Weekends and holidays are not modelled.
"""

from datetime import datetime, time, timedelta

from app.jobs.clock import IST


def _hm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def _bounds(dt_ist: datetime, start: time, end: time) -> tuple[datetime, datetime]:
    day = dt_ist.date()
    return (datetime.combine(day, start, tzinfo=IST), datetime.combine(day, end, tzinfo=IST))


def next_working_time(dt: datetime, start: str = "09:00", end: str = "20:00") -> datetime:
    """dt itself if inside working hours, else the next opening. Returns UTC-aware."""
    s, e = _hm(start), _hm(end)
    local = dt.astimezone(IST)
    open_, close = _bounds(local, s, e)
    if local < open_:
        return open_.astimezone(dt.tzinfo)
    if local >= close:
        return (open_ + timedelta(days=1)).astimezone(dt.tzinfo)
    return dt


def add_working_hours(
    dt: datetime, hours: float, start: str = "09:00", end: str = "20:00"
) -> datetime:
    """dt plus `hours` of working time, skipping the closed hours."""
    s, e = _hm(start), _hm(end)
    remaining = timedelta(hours=hours)
    cur = next_working_time(dt, start, end).astimezone(IST)
    while True:
        _, close = _bounds(cur, s, e)
        if cur + remaining <= close:
            return (cur + remaining).astimezone(dt.tzinfo)
        remaining -= close - cur
        cur = next_working_time(close, start, end).astimezone(IST)


def in_working_hours(dt: datetime, start: str = "09:00", end: str = "20:00") -> bool:
    return next_working_time(dt, start, end) == dt
