"""Demo Control Panel endpoints (platform admin only)."""

import json
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.agents.personas import PERSONAS
from app.api.deps import Admin, BizClock, Db
from app.config import get_settings
from app.db.audit import audit
from app.db.models import AuditLog, Job, Vendor
from app.db.session import get_engine
from app.demo.autoreply import set_settings as set_demo_settings
from app.demo.autoreply import settings as demo_settings
from app.demo.load import status_path
from app.demo.scenarios import SCENARIOS
from app.jobs import handlers  # noqa: F401  registers job handlers
from app.jobs.clock import format_ist
from app.jobs.demo_clock import advance, load_clock, next_event_delta
from app.jobs.queue import run_due
from app.llm.factory import provider_name

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


SETTLE_SECONDS = 30


def _advance_and_run(db: Db, delta: timedelta) -> dict[str, Any]:
    """Move the clock, then run everything due. The background worker may be running some
    of those jobs at the same moment (SKIP LOCKED gives them to one or the other), so wait
    for its running jobs to finish and drain again: the call returns only when the system
    has fully caught up, which keeps the demo deterministic."""
    advance(db, delta)
    make = sessionmaker(get_engine(), expire_on_commit=False)
    ran = 0
    deadline = time.monotonic() + SETTLE_SECONDS
    while True:
        ran += run_due(make, load_clock(db), worker="demo-clock")
        with make() as s:
            busy = (
                s.scalar(select(func.count()).select_from(Job).where(Job.status == "running")) or 0
            )
        if not busy or time.monotonic() > deadline:
            break
        time.sleep(0.1)
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


# --- demo experience (Stage 12) -------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[2]
_running: dict[str, subprocess.Popen[bytes]] = {}


class DemoSettingsIn(BaseModel):
    auto_reply: bool | None = None
    persona_mode: Literal["scripted", "gemini"] | None = None


@router.get("/demo")
def demo_state(_: Admin, db: Db) -> dict[str, Any]:
    _demo_only()
    path = status_path()
    status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": "idle"}
    proc = _running.get("loader")
    if status.get("state") == "running" and (proc is None or proc.poll() is not None):
        status = {**status, "state": "failed" if proc and proc.returncode else status.get("state")}
    return {
        "settings": demo_settings(db),
        "llm": provider_name(),
        "status": status,
        "scenarios": [{"key": k, "name": n} for k, n, _ in sorted(SCENARIOS, key=lambda s: s[0])],
        "personas": list(PERSONAS),
    }


@router.put("/demo/settings")
def update_demo_settings(
    body: DemoSettingsIn, admin: Admin, db: Db, clock: BizClock
) -> dict[str, Any]:
    _demo_only()
    changes = body.model_dump(exclude_none=True)
    out = set_demo_settings(db, **changes)
    audit(db, clock, actor=f"user:{admin.id}", action="demo.settings", entity="demo", after=changes)
    db.commit()
    return out


@router.get("/vendors")
def vendor_personas(_: Admin, db: Db) -> list[dict[str, Any]]:
    return [
        {
            "id": str(v.id),
            "name": v.display_name,
            "persona": v.persona,
            "items": v.item_codes,
            "opted_out": v.opted_out_at is not None,
        }
        for v in db.scalars(select(Vendor).order_by(Vendor.display_name))
    ]


class PersonaIn(BaseModel):
    persona: str


@router.put("/vendors/{vendor_id}/persona")
def set_persona(
    vendor_id: uuid.UUID, body: PersonaIn, admin: Admin, db: Db, clock: BizClock
) -> dict[str, Any]:
    _demo_only()
    if body.persona not in PERSONAS:
        raise HTTPException(422, f"Unknown persona; choose one of {', '.join(PERSONAS)}")
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Not found")
    vendor.persona = body.persona
    audit(
        db,
        clock,
        actor=f"user:{admin.id}",
        action="demo.persona",
        entity="vendor",
        entity_id=vendor.id,
        after={"persona": body.persona},
    )
    db.commit()
    return {"id": str(vendor.id), "persona": vendor.persona}


class LoadIn(BaseModel):
    reset: bool = False
    scenarios: list[str] = Field(default_factory=list)
    play: bool = False


@router.post("/demo/load")
def load_demo(body: LoadIn, _: Admin) -> dict[str, Any]:
    """Runs the loader as a separate process (it drives the API like real users) and
    returns at once; the panel polls /admin/demo for progress."""
    _demo_only()
    proc = _running.get("loader")
    if proc is not None and proc.poll() is None:
        raise HTTPException(409, "A demo load is already running")
    valid = {k for k, _, _ in SCENARIOS}
    if not set(body.scenarios) <= valid:
        raise HTTPException(422, "Unknown scenario")
    args = [sys.executable, "-m", "app.demo.load"]
    if body.reset:
        args.append("--reset")
    for key in body.scenarios:
        args += ["--scenario", key]
    if body.play:
        args.append("--play")
    status_path().parent.mkdir(parents=True, exist_ok=True)
    status_path().write_text(
        json.dumps({"state": "running", "current": "starting", "results": {}}), encoding="utf-8"
    )
    _running["loader"] = subprocess.Popen(args, cwd=BACKEND_DIR)  # noqa: S603  fixed module, validated args
    return {"started": True, "args": args[3:]}
