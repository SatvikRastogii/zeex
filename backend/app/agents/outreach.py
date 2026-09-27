"""Outreach Agent (PROMPT.md 10.2). Plain code.

Send RFQs -> one invite job per vendor at the next working-hours slot, a reminder at
50% of the bid window (non-responders only), and a bid-close job at the close time.
Every job re-checks state when it runs, so running twice changes nothing."""

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import Outbound
from app.channels.simulated import get_channel
from app.db.audit import audit
from app.db.models import Bom, BomLine, BuilderOrg, CatalogItem, Rfq, RfqInvitation, Site, Vendor
from app.domain.settings import org_settings
from app.domain.states import transition
from app.domain.units import format_qty
from app.domain.working_hours import last_working_time, next_working_time
from app.jobs.clock import Clock, format_ist
from app.jobs.queue import enqueue, handler

MAX_INVITES = 15


class OutreachError(Exception):
    pass


def _ctx(db: Session, rfq: Rfq) -> tuple[BuilderOrg, BomLine, CatalogItem, Site, dict[str, Any]]:
    line = db.get(BomLine, rfq.bom_line_id)
    assert line is not None and line.catalog_item_id is not None
    bom = db.get(Bom, line.bom_id)
    item = db.get(CatalogItem, line.catalog_item_id)
    org = db.get(BuilderOrg, rfq.builder_org_id)
    assert bom is not None and item is not None and org is not None
    site = db.get(Site, bom.site_id)
    assert site is not None
    return org, line, item, site, org_settings(org.settings)


def _params(db: Session, rfq: Rfq) -> dict[str, str]:
    org, line, item, site, _ = _ctx(db, rfq)
    assert rfq.bid_window_closes_at is not None
    return {
        "builder": org.name,
        "rfq_code": rfq.public_code,
        "item": item.name,
        "qty": format_qty(line.qty_canonical_milli, item.canonical_unit),
        "area": site.area,  # never the exact address before award
        "needed_by": line.needed_by.strftime("%d %b %Y"),
        "closes_at": format_ist(rfq.bid_window_closes_at),
    }


def quote_form(rfq: Rfq) -> dict[str, object]:
    return {
        "type": "quote_form",
        "rfq_id": str(rfq.id),
        "fields": [
            "unit_price",
            "price_unit",
            "gst_included",
            "gst_percent",
            "freight",
            "delivery_date",
            "validity_until",
            "payment_terms_days",
            "brand",
            "qty_offered",
        ],
    }


def send_rfqs(db: Session, clock: Clock, rfq: Rfq, *, actor: str) -> int:
    """Open the bid window and schedule invites. Returns the number of vendors invited."""
    if rfq.status != "matching":
        raise OutreachError(
            f"{rfq.public_code} is {rfq.status}; RFQs can only be sent from matching"
        )
    invites = db.scalars(
        select(RfqInvitation).where(
            RfqInvitation.rfq_id == rfq.id, RfqInvitation.status == "proposed"
        )
    ).all()
    vendors = {
        v.id: v
        for v in db.scalars(select(Vendor).where(Vendor.id.in_([i.vendor_id for i in invites])))
    }
    sendable: list[RfqInvitation] = []
    skipped: dict[uuid.UUID, str] = {}
    for inv in invites:
        if vendors[inv.vendor_id].opted_out_at is not None:
            skipped[inv.id] = "skipped_opted_out"  # never message an opted-out vendor
        elif len(sendable) >= MAX_INVITES:
            skipped[inv.id] = "skipped_limit"
        else:
            sendable.append(inv)
    if not sendable:
        raise OutreachError("No vendors on the shortlist can be messaged")

    _, _, _, _, cfg = _ctx(db, rfq)
    hours = cfg["working_hours"]
    now = clock.now()
    first_send = next_working_time(now, hours["start"], hours["end"])
    rfq.bid_window_opens_at = first_send
    rfq.bid_window_closes_at = first_send + timedelta(hours=int(cfg["bid_window_hours"]))
    transition(db, clock, "rfq", rfq, "invited", actor=actor)
    transition(db, clock, "rfq", rfq, "bidding", actor=actor)

    for inv in invites:
        if inv.id in skipped:
            inv.status = skipped[inv.id]
            continue
        inv.status = "queued"
        enqueue(
            db,
            "send_invite",
            first_send,
            {"invitation_id": str(inv.id)},
            ordering_key=f"vendor:{inv.vendor_id}",
            dedupe_key=f"invite:{inv.id}:r{rfq.revision}",
        )
    halfway = rfq.bid_window_opens_at + (rfq.bid_window_closes_at - rfq.bid_window_opens_at) / 2
    remind_at = reminder_time(halfway, rfq.bid_window_opens_at, rfq.bid_window_closes_at, hours)
    if remind_at is not None:
        enqueue(
            db,
            "bid_reminder",
            remind_at,
            {"rfq_id": str(rfq.id)},
            dedupe_key=f"reminder:{rfq.id}",
        )
    enqueue(
        db,
        "bid_close",
        rfq.bid_window_closes_at,
        {"rfq_id": str(rfq.id)},
        dedupe_key=f"close:{rfq.id}:1",
    )
    audit(
        db,
        clock,
        actor=actor,
        action="rfq.send",
        entity="rfq",
        entity_id=rfq.id,
        org_id=rfq.builder_org_id,
        after={
            "vendors": len(sendable),
            "first_send": first_send.isoformat(),
            "closes_at": rfq.bid_window_closes_at.isoformat(),
        },
    )
    return len(sendable)


def reminder_time(
    halfway: datetime, opens: datetime, closes: datetime, hours: dict[str, str]
) -> datetime | None:
    """Half-way through the window, moved into working hours. If waiting for the next
    opening would reach the close, send at the last working minute before half-way."""
    at = next_working_time(halfway, hours["start"], hours["end"])
    if at >= closes:
        at = last_working_time(halfway, hours["start"], hours["end"])
    return at if opens < at < closes else None


def _invitation(db: Session, payload: dict[str, object]) -> RfqInvitation | None:
    return db.get(RfqInvitation, uuid.UUID(str(payload["invitation_id"])))


@handler("send_invite")
def send_invite(db: Session, clock: Clock, payload: dict[str, object]) -> None:
    inv = _invitation(db, payload)
    if inv is None or inv.status != "queued":
        return  # already sent, removed or cancelled
    rfq = db.get(Rfq, inv.rfq_id)
    vendor = db.get(Vendor, inv.vendor_id)
    assert rfq is not None and vendor is not None
    if rfq.status != "bidding":
        inv.status = "cancelled"
        return
    msg = get_channel().send(
        db,
        clock,
        Outbound(
            vendor=vendor,
            template="rfq_invite",
            params=_params(db, rfq),
            payload={"form": quote_form(rfq)},
            org_id=rfq.builder_org_id,
            rfq_id=rfq.id,
            invitation_id=inv.id,
        ),
    )
    inv.status = "blocked" if msg.status == "blocked" else "invited"
    inv.invited_at = clock.now()


@handler("bid_reminder")
def bid_reminder(db: Session, clock: Clock, payload: dict[str, object]) -> None:
    rfq = db.get(Rfq, uuid.UUID(str(payload["rfq_id"])))
    if rfq is None or rfq.status != "bidding":
        return
    params = _params(db, rfq)
    for inv in db.scalars(select(RfqInvitation).where(RfqInvitation.rfq_id == rfq.id)):
        if inv.status != "invited" or inv.reminded_at is not None:
            continue  # responded, blocked, or already reminded: one reminder only
        vendor = db.get(Vendor, inv.vendor_id)
        assert vendor is not None
        get_channel().send(
            db,
            clock,
            Outbound(
                vendor=vendor,
                template="bid_reminder",
                params=params,
                payload={"form": quote_form(rfq)},
                org_id=rfq.builder_org_id,
                rfq_id=rfq.id,
                invitation_id=inv.id,
            ),
        )
        inv.reminded_at = clock.now()


# Run after the bid window closes (evaluation registers here in Stage 8).
CLOSE_HOOKS: list[Callable[[Session, Clock, Rfq], None]] = []


@handler("bid_close")
def bid_close(db: Session, clock: Clock, payload: dict[str, object]) -> None:
    rfq = db.get(Rfq, uuid.UUID(str(payload["rfq_id"])))
    if rfq is None or rfq.status != "bidding":
        return
    if rfq.bid_window_closes_at and clock.now() < rfq.bid_window_closes_at:
        return  # window was extended; a later close job exists
    transition(
        db, clock, "rfq", rfq, "evaluating", actor="system:outreach", reason="bid window closed"
    )
    _, _, _, _, cfg = _ctx(db, rfq)
    hours = cfg["working_hours"]
    enqueue(
        db,
        "notify_bid_closed",
        next_working_time(clock.now(), hours["start"], hours["end"]),
        {"rfq_id": str(rfq.id)},
        dedupe_key=f"closed-notice:{rfq.id}:{rfq.bid_window_closes_at}",
    )
    for hook in CLOSE_HOOKS:
        hook(db, clock, rfq)


@handler("notify_bid_closed")
def notify_bid_closed(db: Session, clock: Clock, payload: dict[str, object]) -> None:
    rfq = db.get(Rfq, uuid.UUID(str(payload["rfq_id"])))
    if rfq is None:
        return
    for inv in db.scalars(select(RfqInvitation).where(RfqInvitation.rfq_id == rfq.id)):
        if inv.status not in ("invited", "responded"):
            continue
        vendor = db.get(Vendor, inv.vendor_id)
        assert vendor is not None
        get_channel().send(
            db,
            clock,
            Outbound(
                vendor=vendor,
                template="bid_closed",
                params={"rfq_code": rfq.public_code},
                org_id=rfq.builder_org_id,
                rfq_id=rfq.id,
            ),
        )


def schedule_rfq_update(db: Session, clock: Clock, rfq: Rfq) -> None:
    """A published line changed after invites went out: re-send the RFQ with the change."""
    _, _, _, _, cfg = _ctx(db, rfq)
    hours = cfg["working_hours"]
    enqueue(
        db,
        "rfq_update",
        next_working_time(clock.now(), hours["start"], hours["end"]),
        {"rfq_id": str(rfq.id), "revision": rfq.revision},
        dedupe_key=f"rfq-update:{rfq.id}:r{rfq.revision}",
    )


@handler("rfq_update")
def rfq_update(db: Session, clock: Clock, payload: dict[str, object]) -> None:
    rfq = db.get(Rfq, uuid.UUID(str(payload["rfq_id"])))
    if (
        rfq is None
        or rfq.status not in ("invited", "bidding")
        or rfq.revision != payload["revision"]
    ):
        return  # a newer revision will send its own update
    params = _params(db, rfq)
    for inv in db.scalars(select(RfqInvitation).where(RfqInvitation.rfq_id == rfq.id)):
        if inv.status not in ("invited", "responded"):
            continue
        vendor = db.get(Vendor, inv.vendor_id)
        assert vendor is not None
        get_channel().send(
            db,
            clock,
            Outbound(
                vendor=vendor,
                template="rfq_update",
                params=params,
                payload={"form": quote_form(rfq)},
                org_id=rfq.builder_org_id,
                rfq_id=rfq.id,
                invitation_id=inv.id,
            ),
        )
    rfq.stale = False
