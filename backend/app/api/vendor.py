"""Vendor Inbox (simulated WhatsApp). A vendor sees only their own conversations,
across every builder they work with."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Select, select

from app.agents.quote_intake import form_quote
from app.agents.quote_parser import MAX_FILE_BYTES, VENDOR_MESSAGES
from app.api.deps import BizClock, Db, VendorUser
from app.channels.base import Outbound
from app.channels.inbound import Inbound, receive
from app.channels.simulated import get_channel
from app.config import get_settings
from app.db.models import BuilderOrg, Message, Rfq, RfqInvitation, Vendor
from app.files import get_file_store
from app.jobs.clock import ist_today
from app.llm.schemas import ParsedQuote
from app.seed.sample_docs import build as build_samples

router = APIRouter(prefix="/vendor", tags=["vendor"])

GENERAL = "general"


def _visible(vendor_id: uuid.UUID) -> Select[Message]:
    """What the vendor's phone would show: never messages blocked before delivery."""
    return select(Message).where(Message.vendor_id == vendor_id, Message.status != "blocked")


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
    rows = list(db.scalars(_visible(vendor.id)))
    return [_msg_out(m) for m in _ordered(rows)]


@router.get("/messages/{message_id}")
def my_message(message_id: uuid.UUID, vendor: VendorUser, db: Db) -> dict[str, Any]:
    m = db.get(Message, message_id)
    if m is None or m.vendor_id != vendor.id:
        raise HTTPException(404, "Not found")
    return _msg_out(m)


@router.get("/conversations")
def conversations(vendor: VendorUser, db: Db) -> list[dict[str, Any]]:
    rows = _ordered(list(db.scalars(_visible(vendor.id))))
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
    q = _visible(vendor.id)
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


def _invited_rfq(db: Db, vendor: Vendor, rfq_id: uuid.UUID | None) -> Rfq | None:
    """The RFQ if this vendor was invited to it; 404 otherwise (never leak other RFQs)."""
    if rfq_id is None:
        return None
    invited = db.scalars(
        select(RfqInvitation).where(
            RfqInvitation.rfq_id == rfq_id, RfqInvitation.vendor_id == vendor.id
        )
    ).first()
    rfq = db.get(Rfq, rfq_id)
    if invited is None or rfq is None:
        raise HTTPException(404, "Not found")
    return rfq


class ReplyIn(BaseModel):
    client_message_id: str = Field(min_length=6, max_length=100)
    text: str = Field(default="", max_length=4000)
    button: str | None = Field(default=None, max_length=50)
    reply_to: uuid.UUID | None = None  # the message whose button was tapped
    rfq_id: uuid.UUID | None = None
    sent_at: datetime | None = None


@router.post("/messages")
def reply(body: ReplyIn, vendor: VendorUser, db: Db, clock: BizClock) -> dict[str, Any]:
    """Vendor sends a message. Re-sending the same client_message_id is a no-op."""
    rfq = _invited_rfq(db, vendor, body.rfq_id)
    if not body.text.strip() and not body.button:
        raise HTTPException(422, "Empty message")
    extra: dict[str, Any] = {}
    if body.reply_to is not None:
        original = db.get(Message, body.reply_to)
        if original is None or original.vendor_id != vendor.id:
            raise HTTPException(404, "Not found")
        extra["reply_to"] = str(original.id)
        if "pick_for" in (original.payload or {}):
            extra["pick_for"] = original.payload["pick_for"]
    msg, created = receive(
        db,
        clock,
        Inbound(
            vendor=vendor,
            client_message_id=body.client_message_id,
            text=body.text or (body.button or ""),
            button=body.button,
            rfq_id=body.rfq_id,
            org_id=rfq.builder_org_id if rfq else None,
            sent_at=body.sent_at,
            payload=extra,
        ),
    )
    db.commit()
    return {"message": _msg_out(msg), "duplicate": not created}


class QuoteForm(BaseModel):
    """The 'Submit quote' form. Amounts are rupees as typed (validated, not floats)."""

    client_message_id: str = Field(min_length=6, max_length=100)
    rfq_id: uuid.UUID
    unit_price: str = Field(pattern=r"^\d{1,9}(\.\d{1,2})?$")
    price_unit: Literal["bag", "tonne", "kg", "cft", "brass", "nos", "box"]
    gst_included: bool = False
    gst_percent: str | None = Field(default=None, pattern=r"^\d{1,2}(\.\d{1,2})?$")
    freight: str | None = Field(default=None, pattern=r"^\d{1,9}(\.\d{1,2})?$")
    freight_included: bool = True
    unloading: str | None = Field(default=None, pattern=r"^\d{1,9}(\.\d{1,2})?$")
    delivery_date: date
    validity_until: date
    payment_terms_days: int = Field(ge=0, le=365)
    brand: str | None = Field(default=None, max_length=60)
    qty_offered: str | None = Field(default=None, pattern=r"^\d{1,9}(\.\d{1,3})?$")


@router.post("/quotes")
def submit_form(body: QuoteForm, vendor: VendorUser, db: Db, clock: BizClock) -> dict[str, Any]:
    rfq = _invited_rfq(db, vendor, body.rfq_id)
    assert rfq is not None
    fields = body.model_dump(exclude={"client_message_id", "rfq_id"})
    msg, _ = receive(
        db,
        clock,
        Inbound(
            vendor=vendor,
            client_message_id=body.client_message_id,
            text=f"Quote form: {body.unit_price} per {body.price_unit}",
            rfq_id=rfq.id,
            org_id=rfq.builder_org_id,
            payload={
                "form_submission": {k: str(v) if v is not None else None for k, v in fields.items()}
            },
        ),
    )
    parsed = ParsedQuote.model_validate(
        {**{k: v for k, v in fields.items() if v is not None}, "confidence": 100}
    )
    q = form_quote(db, clock, vendor, rfq, parsed, msg.id)
    db.commit()
    return {"quote_id": str(q.id), "code": q.public_code, "status": q.status, "flags": q.flags}


def _receive_file(
    db: Db,
    clock: BizClock,
    vendor: Vendor,
    rfq: Rfq | None,
    data: bytes,
    filename: str,
    client_id: str,
    caption: str,
) -> Message:
    too_big = len(data) > MAX_FILE_BYTES
    ref = None if too_big else get_file_store().save(f"quotes/{vendor.id}", filename, data)
    msg, created = receive(
        db,
        clock,
        Inbound(
            vendor=vendor,
            client_message_id=client_id,
            text=caption,
            rfq_id=rfq.id if rfq else None,
            org_id=rfq.builder_org_id if rfq else None,
            payload={"file_ref": ref, "filename": filename, "size": len(data)}
            if ref
            else {"filename": filename, "size": len(data)},
        ),
    )
    if created and too_big:
        get_channel().send(
            db,
            clock,
            Outbound(
                vendor=vendor,
                text=VENDOR_MESSAGES["too_large"],
                org_id=msg.builder_org_id,
                rfq_id=msg.rfq_id,
            ),
        )
    return msg


@router.post("/files")
async def upload_file(
    request: Request,
    filename: str,
    client_message_id: str,
    vendor: VendorUser,
    db: Db,
    clock: BizClock,
    rfq_id: uuid.UUID | None = None,
    caption: str = "",
) -> dict[str, Any]:
    """Raw file bytes in the body (PDF or photo), like sending a document on WhatsApp."""
    rfq = _invited_rfq(db, vendor, rfq_id)
    data = await request.body()
    if len(data) > MAX_FILE_BYTES + 1024 * 1024:
        raise HTTPException(413, VENDOR_MESSAGES["too_large"])
    msg = _receive_file(
        db, clock, vendor, rfq, data, filename[:120], client_message_id, caption[:500]
    )
    db.commit()
    return {"message": _msg_out(msg)}


@router.get("/samples")
def samples(_: VendorUser, clock: BizClock) -> list[dict[str, str]]:
    """Demo only: generated sample quotation documents to send from the inbox."""
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not found")
    return [
        {"name": s.name, "label": s.label, "filename": s.filename}
        for s in build_samples(ist_today(clock)).values()
    ]


@router.post("/samples/{name}")
def send_sample(
    name: str,
    client_message_id: str,
    vendor: VendorUser,
    db: Db,
    clock: BizClock,
    rfq_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if not get_settings().demo_mode:
        raise HTTPException(404, "Not found")
    sample = build_samples(ist_today(clock)).get(name)
    if sample is None:
        raise HTTPException(404, "Not found")
    rfq = _invited_rfq(db, vendor, rfq_id)
    msg = _receive_file(db, clock, vendor, rfq, sample.data, sample.filename, client_message_id, "")
    db.commit()
    return {"message": _msg_out(msg)}
