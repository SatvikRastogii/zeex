from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Bom
from app.domain.states import MACHINES, InvalidTransition, can_transition, transition
from app.jobs.clock import FixedClock
from tests.factories import org_with_site

CLOCK = FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC))


def test_every_target_is_a_known_state() -> None:
    for name, machine in MACHINES.items():
        for src, targets in machine.items():
            assert targets <= set(machine), f"{name}.{src} -> unknown {targets - set(machine)}"


@pytest.mark.parametrize(
    ("entity", "src", "dst", "ok"),
    [
        ("bom", "draft", "validated", True),
        ("bom", "draft", "published", False),
        ("bom", "in_progress", "cancelled", True),
        ("bom", "awarded", "cancelled", False),
        ("rfq", "matching", "no_vendors_matched", True),
        ("rfq", "closed", "bidding", False),
        ("quote", "confirmed", "superseded", True),
        ("quote", "withdrawn", "confirmed", False),
        ("negotiation", "awaiting_reply", "timed_out", True),
        ("negotiation", "closed", "open", False),
        ("work_order", "issued", "vendor_declined", True),
        ("work_order", "closed", "issued", False),
    ],
)
def test_transition_table(entity: str, src: str, dst: str, ok: bool) -> None:
    assert can_transition(entity, src, dst) is ok


def test_transition_updates_and_audits(db: Session) -> None:
    org, site = org_with_site(db)
    bom = Bom(builder_org_id=org.id, public_code="BOM-T-1", site_id=site.id, status="draft")
    db.add(bom)
    db.flush()
    transition(db, CLOCK, "bom", bom, "validated", actor="system:test")
    db.commit()
    assert bom.status == "validated"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "bom.validated")).one()
    assert entry.before == {"status": "draft"} and entry.after == {"status": "validated"}


def test_invalid_transition_raises_and_leaves_state(db: Session) -> None:
    org, site = org_with_site(db)
    bom = Bom(builder_org_id=org.id, public_code="BOM-T-2", site_id=site.id, status="draft")
    db.add(bom)
    db.flush()
    with pytest.raises(InvalidTransition):
        transition(db, CLOCK, "bom", bom, "closed", actor="system:test")
    assert bom.status == "draft"
