"""Durable job queue in Postgres (stands in for Step Functions / EventBridge / SQS FIFO).

- enqueue: at-most-once per dedupe_key.
- claim: FOR UPDATE SKIP LOCKED, oldest due first. Jobs sharing an ordering_key run
  strictly in enqueue order (FIFO), even when an earlier one is waiting to retry.
- run: each job in its own transaction; failures retry with backoff, then fail.
- recover_stale: jobs left 'running' by a dead worker go back to pending.
Handlers must be idempotent: a job can run again after a crash.
"""

import logging
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job
from app.jobs.clock import Clock

log = logging.getLogger("jobs")

MAX_ATTEMPTS = 5
STALE_AFTER = timedelta(minutes=5)

Handler = Callable[[Session, Clock, dict[str, Any]], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


def enqueue(
    db: Session,
    kind: str,
    run_at: datetime,
    payload: dict[str, Any] | None = None,
    *,
    ordering_key: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    stmt = insert(Job).values(
        id=uuid.uuid4(),
        kind=kind,
        run_at=run_at,
        payload=payload or {},
        ordering_key=ordering_key,
        dedupe_key=dedupe_key,
        status="pending",
        attempts=0,
    )
    db.execute(stmt.on_conflict_do_nothing(index_elements=["dedupe_key"]))


_CLAIM = text("""
    SELECT j.id FROM jobs j
    WHERE j.status = 'pending' AND j.run_at <= :now
      AND NOT EXISTS (
        SELECT 1 FROM jobs e
        WHERE e.ordering_key = j.ordering_key AND e.id <> j.id
          AND (e.status = 'running' OR (e.status = 'pending' AND e.seq < j.seq))
      )
    ORDER BY j.run_at, j.seq
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
""")


def claim(db: Session, worker: str, now: datetime, limit: int = 1) -> list[Job]:
    ids = [r[0] for r in db.execute(_CLAIM, {"now": now, "limit": limit})]
    if not ids:
        db.commit()
        return []
    db.execute(
        update(Job)
        .where(Job.id.in_(ids))
        .values(status="running", locked_by=worker, locked_at=now, attempts=Job.attempts + 1)
    )
    db.commit()
    jobs = [db.get(Job, i) for i in ids]
    return sorted((j for j in jobs if j), key=lambda j: (j.run_at, j.seq))


def _finish(db: Session, job_id: uuid.UUID, now: datetime, error: str | None) -> None:
    job = db.get(Job, job_id)
    assert job is not None
    if error is None:
        job.status, job.last_error = "done", None
    elif job.attempts >= MAX_ATTEMPTS:
        job.status, job.last_error = "failed", error
        log.error("job %s (%s) failed permanently: %s", job.id, job.kind, error.splitlines()[-1])
    else:
        job.status, job.last_error = "pending", error
        job.run_at = now + timedelta(minutes=2 ** (job.attempts - 1))  # 1, 2, 4, 8 min
    job.locked_by = job.locked_at = None
    db.commit()


def run_job(make_session: sessionmaker[Session], clock: Clock, job: Job) -> bool:
    """Run one claimed job in its own transaction. Returns True on success."""
    fn = HANDLERS.get(job.kind)
    with make_session() as s:
        try:
            if fn is None:
                raise LookupError(f"no handler for job kind {job.kind!r}")
            fn(s, clock, dict(job.payload))
            s.commit()
            error = None
        except Exception:
            s.rollback()
            error = traceback.format_exc(limit=5)
    with make_session() as s:
        _finish(s, job.id, clock.now(), error)
    return error is None


def recover_stale(db: Session, now: datetime) -> int:
    res = db.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < now - STALE_AFTER)
        .values(status="pending", locked_by=None, locked_at=None)
    )
    db.commit()
    return res.rowcount or 0  # type: ignore[attr-defined]


def run_due(
    make_session: sessionmaker[Session], clock: Clock, worker: str = "inline", max_jobs: int = 500
) -> int:
    """Run every due job, oldest first, until none are due. Returns how many ran.

    Used when the demo clock jumps. Each job runs *at its own scheduled time*
    (a clock pinned to run_at), so a close at 14:05 on day 1 behaves as if it fired
    then, even when the jump lands on day 3. Jobs created by handlers that are
    already due run in the same call, so every deadline fires, in order."""
    ran = 0
    while ran < max_jobs:
        with make_session() as s:
            jobs = claim(s, worker, clock.now(), limit=1)
        if not jobs:
            return ran
        job = jobs[0]
        at = job.run_at if job.run_at < clock.now() else clock.now()
        run_job(make_session, _Pinned(at), job)
        ran += 1
    return ran


class _Pinned:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at
