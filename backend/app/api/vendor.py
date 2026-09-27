"""Vendor Inbox (simulated WhatsApp). A vendor sees only their own conversations,
across every builder they work with."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import BizClock, Db, VendorUser
from app.channels.inbound import Inbound, receive
from app.db.models import BuilderOrg, Message, Rfq, RfqInvitation

router = APIRouter(prefix="/vendor", tags=["vendor"])

GENERAL = "general"


def conv_key(m: Message) -> str:
    return str(m.rfq_id) if m.rfq_id else GENERAL


def _msg_out(m: Message) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "direction": m.direction,
        "body": m.body,
        "payload": m.payload,
        "template": m.template_name,
        "status": m.status,
        "sent_at": m.sent_at,
        "rfq_id": str(m.rfq_id) if m.rfq_id else None,
    }


def _ordered(rows: list[Message]) -> list[Message]:
    # Device time first (messages can arrive out of order), then arrival.
    return sorted(rows, key=lambda m: (m.sent_at or m.created_at, m.created_at))


@router.get("/messages")
def my_messages(vendor: VendorUser, db: Db) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(Message).where(Message.vendor_id == vendor.id)))
    return [_msg_out(m) for m in _ordered(rows)]


@router.get("/messages/{message_id}")
def my_message(message_id: uuid.UUID, vendor: VendorUser, db: Db) -> dict[str, Any]:
    m = db.get(Message, message_id)
    if m is None or m.vendor_id != vendor.id:
        raise HTTPException(404, "Not found")
    return _msg_out(m)


@router.get("/conversations")
def conversations(vendor: VendorUser, db: Db) -> list[dict[str, Any]]:
    rows = _ordered(list(db.scalars(select(Message).where(Message.vendor_id == vendor.id))))
    rfqs = {
        r.id: r
        for r in db.scalars(select(Rfq).where(Rfq.id.in_({m.rfq_id for m in rows if m.rfq_id})))
    }
    orgs = {o.id: o.name for o in db.scalars(select(BuilderOrg))}
    convs: dict[str, dict[str, Any]] = {}
    for m in rows:
        key = conv_key(m)
        rfq = rfqs.get(m.rfq_id) if m.rfq_id else None
        c = convs.setdefault(
            key,
            {
                "key": key,
                "builder": orgs.get(rfq.builder_org_id, "") if rfq else "Z-Procure",
                "rfq_code": rfq.public_code if rfq else None,
                "count": 0,
            },
        )
        c["count"] += 1
        c["last_body"] = m.body[:80]
        c["last_at"] = m.sent_at
    return sorted(convs.values(), key=lambda c: c["last_at"] or datetime.min, reverse=True)


@router.get("/conversations/{key}")
def conversation(key: str, vendor: VendorUser, db: Db) -> dict[str, Any]:
    q = select(Message).where(Message.vendor_id == vendor.id)
    if key == GENERAL:
        q = q.where(Message.rfq_id.is_(None))
    else:
        try:
            q = q.where(Message.rfq_id == uuid.UUID(key))
        except ValueError:
            raise HTTPException(404, "Not found") from None
    rows = _ordered(list(db.scalars(q)))
    if not rows:
        raise HTTPException(404, "Not found")
    return {
        "key": key,
        "opted_out": vendor.opted_out_at is not None,
        "messages": [_msg_out(m) for m in rows],
    }


class ReplyIn(BaseModel):
    client_message_id: str = Field(min_length=6, max_length=100)
    text: str = Field(default="", max_length=4000)
    button: str | None = Field(default=None, max_length=50)
    rfq_id: uuid.UUID | None = None
    sent_at: datetime | None = None


@router.post("/messages")
def reply(body: ReplyIn, vendor: VendorUser, db: Db, clock: BizClock) -> dict[str, Any]:
    """Vendor sends a message. Re-sending the same client_message_id is a no-op."""
    org_id = None
    if body.rfq_id is not None:
        invited = db.scalars(
            select(RfqInvitation).where(
                RfqInvitation.rfq_id == body.rfq_id, RfqInvitation.vendor_id == vendor.id
            )
        ).first()
        rfq = db.get(Rfq, body.rfq_id)
        if invited is None or rfq is None:
            raise HTTPException(404, "Not found")
        org_id = rfq.builder_org_id
    if not body.text.strip() and not body.button:
        raise HTTPException(422, "Empty message")
    msg, created = receive(
        db,
        clock,
        Inbound(
            vendor=vendor,
            client_message_id=body.client_message_id,
            text=body.text or (body.button or ""),
            button=body.button,
            rfq_id=body.rfq_id,
            org_id=org_id,
            sent_at=body.sent_at,
        ),
    )
    db.commit()
    return {"message": _msg_out(msg), "duplicate": not created}
