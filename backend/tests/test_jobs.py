"""Durable job queue: ordering, retries, crashes, locking."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job
from app.db.session import get_engine
from app.jobs.clock import Clock, FixedClock
from app.jobs.queue import _CLAIM, MAX_ATTEMPTS, claim, enqueue, handler, recover_stale, run_due

T0 = datetime(2026, 9, 25, 8, 35, tzinfo=UTC)
RAN: list[str] = []
FAIL_TIMES: dict[str, int] = {}


@handler("test.record")
def _record(db: Session, clock: Clock, payload: dict[str, Any]) -> None:
    name = str(payload["name"])
    if FAIL_TIMES.get(name, 0) > 0:
        FAIL_TIMES[name] -= 1
        raise RuntimeError(f"simulated crash in {name}")
    RAN.append(name)


@handler("test.chain")
def _chain(db: Session, clock: Clock, payload: dict[str, Any]) -> None:
    RAN.append("chain")
    enqueue(db, "test.record", clock.now(), {"name": "spawned"})  # already due


@pytest.fixture(autouse=True)
def _reset() -> None:
    RAN.clear()
    FAIL_TIMES.clear()


@pytest.fixture
def make(db: Session) -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def add(db: Session, name: str, at: datetime, **kw: Any) -> None:
    enqueue(db, "test.record", at, {"name": name}, **kw)
    db.commit()


def test_dedupe_key_enqueues_once(db: Session) -> None:
    add(db, "a", T0, dedupe_key="k1")
    add(db, "a", T0, dedupe_key="k1")
    assert db.scalar(select(text("count(*)")).select_from(Job)) == 1


def test_not_due_jobs_wait(db: Session, make: sessionmaker[Session]) -> None:
    add(db, "later", T0 + timedelta(hours=1))
    assert run_due(make, FixedClock(T0)) == 0 and RAN == []


def test_clock_jump_fires_all_due_jobs_in_order(db: Session, make: sessionmaker[Session]) -> None:
    add(db, "third", T0 + timedelta(hours=3))
    add(db, "first", T0 + timedelta(hours=1))
    add(db, "second-a", T0 + timedelta(hours=2))
    add(db, "second-b", T0 + timedelta(hours=2))  # same time: enqueue order
    add(db, "future", T0 + timedelta(days=2))
    assert run_due(make, FixedClock(T0 + timedelta(hours=5))) == 4
    assert RAN == ["first", "second-a", "second-b", "third"]


def test_jobs_created_by_jobs_also_run(db: Session, make: sessionmaker[Session]) -> None:
    enqueue(db, "test.chain", T0, {})
    db.commit()
    run_due(make, FixedClock(T0))
    assert RAN == ["chain", "spawned"]


def test_crash_is_retried_with_backoff_then_succeeds(
    db: Session, make: sessionmaker[Session]
) -> None:
    FAIL_TIMES["flaky"] = 1
    add(db, "flaky", T0)
    clock = FixedClock(T0)
    run_due(make, clock)
    job = db.scalars(select(Job)).one()
    db.refresh(job)
    assert (
        job.status == "pending"
        and job.attempts == 1
        and "simulated crash" in (job.last_error or "")
    )
    assert job.run_at == T0 + timedelta(minutes=1)
    clock.advance(timedelta(minutes=1))
    run_due(make, clock)
    db.refresh(job)
    assert job.status == "done" and RAN == ["flaky"]


def test_gives_up_after_max_attempts(db: Session, make: sessionmaker[Session]) -> None:
    FAIL_TIMES["broken"] = 99
    add(db, "broken", T0)
    clock = FixedClock(T0)
    for _ in range(MAX_ATTEMPTS):
        run_due(make, clock)
        clock.advance(timedelta(hours=1))
    job = db.scalars(select(Job)).one()
    db.refresh(job)
    assert job.status == "failed" and job.attempts == MAX_ATTEMPTS and RAN == []


def test_unknown_kind_fails_without_crashing(db: Session, make: sessionmaker[Session]) -> None:
    enqueue(db, "no.such.kind", T0, {})
    db.commit()
    assert run_due(make, FixedClock(T0)) == 1
    assert "no handler" in (db.scalars(select(Job.last_error)).one() or "")


def test_ordering_key_is_fifo_even_across_retries(db: Session, make: sessionmaker[Session]) -> None:
    FAIL_TIMES["m1"] = 1
    add(db, "m1", T0, ordering_key="vendor:1")
    add(db, "m2", T0, ordering_key="vendor:1")
    add(db, "other", T0, ordering_key="vendor:2")
    clock = FixedClock(T0)
    run_due(make, clock)
    assert RAN == ["other"]  # m1 crashed and waits to retry; m2 must not overtake it
    clock.advance(timedelta(minutes=1))
    run_due(make, clock)
    assert RAN == ["other", "m1", "m2"]


def test_skip_locked_claims_are_disjoint(db: Session, make: sessionmaker[Session]) -> None:
    add(db, "a", T0)
    add(db, "b", T0)
    with make() as a:
        locked = [r[0] for r in a.execute(_CLAIM, {"now": T0, "limit": 1})]  # holds the row lock
        with make() as b:
            got = claim(b, "worker-b", T0, limit=5)
        assert len(locked) == 1 and [j.id for j in got] != locked and len(got) == 1
        a.rollback()


def test_stale_running_job_recovered_after_worker_restart(
    db: Session, make: sessionmaker[Session]
) -> None:
    add(db, "orphan", T0)
    with make() as s:
        claim(s, "dead-worker", T0)  # worker dies before running it
    assert run_due(make, FixedClock(T0)) == 0  # still locked as running
    with make() as s:
        assert recover_stale(s, T0 + timedelta(minutes=6)) == 1
    run_due(make, FixedClock(T0 + timedelta(minutes=6)))
    assert RAN == ["orphan"]


def test_fresh_running_job_not_recovered(db: Session, make: sessionmaker[Session]) -> None:
    add(db, "busy", T0)
    with make() as s:
        claim(s, "live-worker", T0)
        assert recover_stale(s, T0 + timedelta(minutes=1)) == 0
    db.execute(update(Job).values(status="done"))
    db.commit()


seen_at: list[datetime] = []


@handler("test.when")
def _when(db: Session, clock: Clock, payload: dict[str, Any]) -> None:
    seen_at.append(clock.now())


def test_catch_up_runs_each_job_at_its_own_time(db: Session, make: sessionmaker[Session]) -> None:
    seen_at.clear()
    for h in (1, 3):
        enqueue(db, "test.when", T0 + timedelta(hours=h), {})
    db.commit()
    run_due(make, FixedClock(T0 + timedelta(days=2)))  # one big jump
    assert seen_at == [T0 + timedelta(hours=1), T0 + timedelta(hours=3)]
