import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

# Point the app at the test database before anything creates an engine.
os.environ["DATABASE_URL"] = get_settings().test_database_url
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
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        for seq in ("bom_code_seq", "quote_code_seq", "neg_code_seq", "po_code_seq"):
            conn.execute(text(f"ALTER SEQUENCE {seq} RESTART"))


@pytest.fixture
def db() -> Iterator[Session]:
    _truncate()
    with sessionmaker(get_engine(), expire_on_commit=False)() as s:
        yield s
    _truncate()
