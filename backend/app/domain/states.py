"""Explicit transition tables (PROMPT.md section 9). Anything not listed is refused."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.audit import audit
from app.jobs.clock import Clock

log = logging.getLogger("states")

_BOM_OPEN = [
    "draft",
    "validated",
    "published",
    "in_progress",
    "awaiting_approval",
    "partially_awarded",
]

BOM: dict[str, set[str]] = {
    "draft": {"validated"},
    "validated": {"published"},
    "published": {"in_progress"},
    "in_progress": {"awaiting_approval"},
    "awaiting_approval": {"partially_awarded", "awarded", "in_progress"},
    "partially_awarded": {"awarded"},
    "awarded": {"closed"},
    "closed": set(),
    "cancelled": set(),
}
for _s in _BOM_OPEN:
    BOM[_s] = BOM[_s] | {"cancelled"}

_RFQ_SIDE_EXITS = {"cancelled", "failed"}
RFQ: dict[str, set[str]] = {
    "draft": {"matching"},
    "matching": {"invited", "no_vendors_matched"},
    "invited": {"bidding"},
    "bidding": {"evaluating"},
    # insufficient_quotes can reopen bidding once (window extension)
    "evaluating": {"negotiating", "awaiting_approval", "insufficient_quotes"},
    "insufficient_quotes": {"bidding", "evaluating"},
    "negotiating": {"awaiting_approval"},
    # "Compare again" / runner-up / re-negotiate go back a step
    "awaiting_approval": {"awarded", "negotiating", "evaluating"},
    "awarded": {"closed", "awaiting_approval"},
    "no_vendors_matched": {"matching"},
    "closed": set(),
    "cancelled": set(),
    "failed": set(),
}
for _s, _nxt in RFQ.items():
    if _s not in {"closed", "cancelled", "failed", "awarded"}:
        _nxt |= _RFQ_SIDE_EXITS
RFQ["awarded"] |= {"cancelled"}

QUOTE: dict[str, set[str]] = {
    "draft_parsed": {"awaiting_confirmation", "confirmed", "rejected"},
    "awaiting_confirmation": {"confirmed", "draft_parsed", "withdrawn", "rejected"},
    "confirmed": {"superseded", "withdrawn", "expired", "rejected"},
    "superseded": set(),
    "withdrawn": set(),
    "expired": set(),
    "rejected": set(),
}

NEGOTIATION: dict[str, set[str]] = {
    "open": {"counter_sent", "needs_human", "closed"},
    "counter_sent": {"awaiting_reply"},
    "awaiting_reply": {"countered", "final_offer", "declined", "timed_out", "needs_human"},
    "countered": {"counter_sent", "final_offer", "needs_human"},
    "final_offer": {"closed", "needs_human"},
    "declined": {"closed"},
    "timed_out": {"closed"},
    "needs_human": {"closed"},
    "closed": set(),
}
for _s in ("open", "counter_sent", "awaiting_reply", "countered", "final_offer"):
    NEGOTIATION[_s] |= {"closed", "needs_human"}

WORK_ORDER: dict[str, set[str]] = {
    "issued": {"vendor_confirmed", "vendor_declined", "expired", "cancelled"},
    "vendor_confirmed": {"in_delivery", "cancelled"},
    "in_delivery": {"delivered", "cancelled"},
    "delivered": {"closed", "in_delivery"},
    "vendor_declined": set(),
    "expired": set(),
    "cancelled": set(),
    "closed": set(),
}

MACHINES = {
    "bom": BOM,
    "rfq": RFQ,
    "quote": QUOTE,
    "negotiation": NEGOTIATION,
    "work_order": WORK_ORDER,
}


class InvalidTransition(Exception):
    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(f"{entity}: cannot go from {current} to {target}")
        self.entity, self.current, self.target = entity, current, target


def can_transition(entity: str, current: str, target: str) -> bool:
    return target in MACHINES[entity].get(current, set())


def transition(
    db: Session,
    clock: Clock,
    entity: str,
    row: Any,
    target: str,
    *,
    actor: str,
    field: str = "status",
    reason: str | None = None,
) -> None:
    """Move row.<field> to target or raise InvalidTransition. Always audited."""
    current = getattr(row, field)
    if not can_transition(entity, current, target):
        # Logged, not audited: the caller's transaction is about to roll back.
        log.warning("refused %s %s: %s -> %s (%s)", entity, row.id, current, target, actor)
        raise InvalidTransition(entity, current, target)
    setattr(row, field, target)
    audit(
        db,
        clock,
        actor=actor,
        action=f"{entity}.{target}",
        entity=entity,
        entity_id=row.id,
        org_id=getattr(row, "builder_org_id", None),
        before={field: current},
        after={field: target, "reason": reason} if reason else {field: target},
    )
