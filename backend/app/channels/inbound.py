"""Inbound vendor messages: dedupe, store, then route.

Vendor text is untrusted. Here it is only stored and checked for STOP/START;
later stages hand it to the quote parser or the negotiation reply parser."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import Outbound
from app.channels.simulated import get_channel
from app.db.audit import audit
from app.db.models import Message, Vendor
from app.jobs.clock import Clock

STOP_WORDS = {"stop", "unsubscribe", "stop all", "बंद", "रोको"}
START_WORDS = {"start", "subscribe", "शुरू"}


@dataclass
class Inbound:
    vendor: Vendor
    client_message_id: str  # the channel's message id (WhatsApp wamid in production)
    text: str = ""
    button: str | None = None
    rfq_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    sent_at: datetime | None = None  # device time; messages can arrive out of order
    payload: dict[str, Any] | None = None


# Routers registered by later stages: (db, clock, message) -> handled?
ROUTERS: list[Callable[[Session, Clock, Message], bool]] = []


def external_id(vendor_id: uuid.UUID, client_message_id: str) -> str:
    return f"sim-in-{vendor_id}-{client_message_id}"


def receive(db: Session, clock: Clock, inb: Inbound) -> tuple[Message, bool]:
    """Store an inbound message once. Returns (message, created). Duplicates are no-ops."""
    ext = external_id(inb.vendor.id, inb.client_message_id)
    existing = db.scalars(select(Message).where(Message.external_message_id == ext)).one_or_none()
    if existing is not None:
        return existing, False
    now = clock.now()
    m = Message(
        builder_org_id=inb.org_id,
        vendor_id=inb.vendor.id,
        rfq_id=inb.rfq_id,
        direction="in",
        channel="simulated_whatsapp",
        external_message_id=ext,
        body=inb.text[:4000],
        payload={
            **(inb.payload or {}),
            **({"button": inb.button} if inb.button else {}),
            "received_at": now.isoformat(),
        },
        sent_at=min(inb.sent_at or now, now),  # a device clock ahead of ours is capped
        delivered_at=now,
        status="received",
    )
    db.add(m)
    db.flush()
    word = " ".join(inb.text.strip().lower().split())
    if word in STOP_WORDS:
        opt_out(db, clock, inb.vendor)
    elif word in START_WORDS:
        opt_in(db, clock, inb.vendor)
    else:
        for route in ROUTERS:
            if route(db, clock, m):
                break
    return m, True


def opt_out(db: Session, clock: Clock, vendor: Vendor) -> None:
    """Immediate and global: applies to every builder."""
    if vendor.opted_out_at is None:
        vendor.opted_out_at = clock.now()
        audit(
            db,
            clock,
            actor=f"vendor:{vendor.id}",
            action="vendor.opt_out",
            entity="vendor",
            entity_id=vendor.id,
        )
    get_channel().send(db, clock, Outbound(vendor=vendor, template="opt_out_confirm"))


def opt_in(db: Session, clock: Clock, vendor: Vendor) -> None:
    if vendor.opted_out_at is not None:
        vendor.opted_out_at = None
        vendor.opted_in_at = clock.now()
        audit(
            db,
            clock,
            actor=f"vendor:{vendor.id}",
            action="vendor.opt_in",
            entity="vendor",
            entity_id=vendor.id,
        )
        get_channel().send(db, clock, Outbound(vendor=vendor, template="opt_in_confirm"))
