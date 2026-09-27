import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
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

from app.api.deps import get_clock, get_wall_clock  # noqa: E402
from app.jobs.clock import FixedClock  # noqa: E402
from app.main import app  # noqa: E402
from app.seed.run import seed_static  # noqa: E402


@pytest.fixture
def seeded(db: Session) -> Session:
    """Orgs, users, sites, catalog and vendors (no history)."""
    seed_static(db, FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC)))
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
        c = TestClient(app)
        code = c.post("/api/auth/otp/request", json={"phone": phone}).json()["demo_otp"]
        r = c.post("/api/auth/otp/verify", json={"phone": phone, "code": code})
        assert r.status_code == 200, r.text
        return c

    return _login
