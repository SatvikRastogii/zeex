import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

# Point the app at the test database (and a throwaway storage folder) before anything
# creates an engine or writes a file.
os.environ["DATABASE_URL"] = get_settings().test_database_url
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="zp-test-storage-")
get_settings.cache_clear()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db.models import Base  # noqa: E402
from app.db.session import get_engine  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def _truncate() -> None:
    """Empty every table. DELETE with triggers off (replica role) is much faster than
    TRUNCATE on small tables, and bypasses the audit_log guard for test cleanup only."""
    with get_engine().begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        for seq in ("bom_code_seq", "quote_code_seq", "neg_code_seq", "po_code_seq"):
            conn.execute(text(f"ALTER SEQUENCE {seq} RESTART"))


@pytest.fixture
def db() -> Iterator[Session]:
    _truncate()
    with sessionmaker(get_engine(), expire_on_commit=False)() as s:
        yield s


# --- API fixtures -------------------------------------------------------------------

from collections.abc import Callable  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import COOKIE, get_clock, get_wall_clock, issue_token  # noqa: E402
from app.auth.otp import find_subject  # noqa: E402
from app.jobs.clock import FixedClock  # noqa: E402
from app.main import app  # noqa: E402
from app.seed.run import seed_static  # noqa: E402

_SEED_ROWS: list[tuple[Any, list[dict[str, Any]]]] | None = None


@pytest.fixture
def seeded(db: Session) -> Session:
    """Orgs, users, sites, catalog and vendors (no history).

    The seed runs once per session; later tests bulk-insert a snapshot of its rows,
    which is much faster than re-running ~150 upserts."""
    global _SEED_ROWS
    if _SEED_ROWS is None:
        seed_static(db, FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC)))
        db.commit()
        _SEED_ROWS = [
            (t, [dict(r._mapping) for r in db.execute(select(t))])
            for t in Base.metadata.sorted_tables
        ]
    else:
        for table, rows in _SEED_ROWS:
            if rows:
                db.execute(insert(table), rows)
        db.commit()
    return db


@pytest.fixture
def wall() -> Iterator[FixedClock]:
    """Controllable wall clock for auth (OTP and session expiry)."""
    clock = FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC))
    app.dependency_overrides[get_wall_clock] = lambda: clock
    yield clock
    app.dependency_overrides.pop(get_wall_clock, None)


BIZ_START = datetime(2026, 9, 25, 8, 35, tzinfo=UTC)  # 25 Sep 2026 14:05 IST


@pytest.fixture
def biz() -> Iterator[FixedClock]:
    """Controllable business clock (what the demo clock drives)."""
    clock = FixedClock(BIZ_START)
    app.dependency_overrides[get_clock] = lambda: clock
    yield clock
    app.dependency_overrides.pop(get_clock, None)


@pytest.fixture
def login(seeded: Session, wall: FixedClock, biz: FixedClock) -> Callable[[str], TestClient]:
    """login(phone) -> a TestClient holding that principal's session cookie."""

    def _login(phone: str) -> TestClient:
        # Issue the session directly: the OTP flow itself is covered in test_auth.py,
        # and argon2 on every login would dominate the suite's run time.
        subject = find_subject(seeded, phone)
        assert subject is not None, phone
        token = issue_token(seeded, subject[0], subject[1], wall)
        return TestClient(app, cookies={COOKIE: token})

    return _login
