"""Demo Control Panel endpoints (platform admin only)."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.deps import Admin, BizClock, Db
from app.config import get_settings
from app.db.models import AuditLog, Job
from app.db.session import get_engine
from app.jobs import handlers  # noqa: F401  registers job handlers
from app.jobs.clock import format_ist
from app.jobs.demo_clock import advance, load_clock, next_event_delta
from app.jobs.queue import run_due

router = APIRouter(prefix="/admin", tags=["admin"])


def _demo_only() -> None:
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not found")


def _clock_out(db: Db) -> dict[str, Any]:
    now = load_clock(db).now()
    return {"now": now, "display": format_ist(now)}


@router.get("/clock")
def clock_state(_: Admin, clock: BizClock) -> dict[str, Any]:
    return {"now": clock.now(), "display": format_ist(clock.now())}


def _advance_and_run(db: Db, delta: timedelta) -> dict[str, Any]:
    advance(db, delta)
    ran = run_due(
        sessionmaker(get_engine(), expire_on_commit=False), load_clock(db), worker="demo-clock"
    )
    return {**_clock_out(db), "jobs_ran": ran}


class AdvanceIn(BaseModel):
    minutes: int = Field(ge=1, le=7 * 24 * 60)


@router.post("/clock/advance")
def clock_advance(body: AdvanceIn, _: Admin, db: Db) -> dict[str, Any]:
    """Move the demo clock forward and run every job that became due, in order."""
    _demo_only()
    return _advance_and_run(db, timedelta(minutes=body.minutes))


@router.post("/clock/next-event")
def clock_next_event(_: Admin, db: Db) -> dict[str, Any]:
    _demo_only()
    delta = next_event_delta(db, load_clock(db))
    if delta is None:
        return {**_clock_out(db), "jobs_ran": 0, "note": "No pending jobs"}
    return _advance_and_run(db, delta)


@router.get("/jobs")
def jobs(_: Admin, db: Db, status: str = "pending") -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Job).where(Job.status == status).order_by(Job.run_at, Job.seq).limit(200)
    )
    return [
        {
            "id": str(j.id),
            "kind": j.kind,
            "run_at": j.run_at,
            "run_at_display": format_ist(j.run_at),
            "attempts": j.attempts,
            "last_error": (j.last_error or "").strip().splitlines()[-1:] or None,
            "payload": j.payload,
        }
        for j in rows
    ]


@router.get("/events")
def events(_: Admin, db: Db, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AuditLog)
        .order_by(AuditLog.at.desc(), AuditLog.created_at.desc())
        .limit(min(limit, 500))
    )
    return [
        {
            "at": a.at,
            "at_display": format_ist(a.at),
            "actor": a.actor,
            "action": a.action,
            "entity": a.entity,
            "after": a.after,
        }
        for a in rows
    ]
