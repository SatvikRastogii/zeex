from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.audit import audit
from app.jobs.clock import FixedClock

CLOCK = FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC))


def _one_entry(db: Session) -> None:
    audit(db, CLOCK, actor="system:test", action="create", entity="bom", after={"a": 1})
    db.commit()


def test_audit_insert_works(db: Session) -> None:
    _one_entry(db)
    assert db.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1


@pytest.mark.parametrize(
    "sql", ["UPDATE audit_log SET action = 'tampered'", "DELETE FROM audit_log"]
)
def test_audit_update_and_delete_rejected(db: Session, sql: str) -> None:
    _one_entry(db)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text(sql))
    db.rollback()
    assert db.execute(text("SELECT action FROM audit_log")).scalar_one() == "create"
