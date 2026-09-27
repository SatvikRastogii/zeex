"""The business clock: real time in production, real time + a stored offset in demo mode.

The offset lives in the database so the API and the worker agree on 'now'."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import DemoClockState, Job
from app.jobs.clock import Clock, DemoClock, SystemClock


def load_clock(db: Session) -> Clock:
    if not get_settings().demo_mode:
        return SystemClock()
    state = db.get(DemoClockState, 1)
    return DemoClock(timedelta(seconds=state.offset_seconds if state else 0))


def advance(db: Session, delta: timedelta) -> None:
    if delta < timedelta(0):
        raise ValueError("the demo clock only moves forward")
    state = db.get(DemoClockState, 1, with_for_update=True)
    if state is None:
        state = DemoClockState(id=1, offset_seconds=0)
        db.add(state)
    state.offset_seconds += int(delta.total_seconds())
    db.commit()


def next_event_delta(db: Session, clock: Clock) -> timedelta | None:
    """How far to jump so the next pending job becomes due (None if nothing is pending)."""
    run_at = db.scalars(
        select(Job.run_at).where(Job.status == "pending").order_by(Job.run_at).limit(1)
    ).first()
    if run_at is None:
        return None
    return max(run_at - clock.now(), timedelta(0)) + timedelta(seconds=1)
