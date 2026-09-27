"""Quote intake: route vendor messages to the parser and run the confirm/edit/
withdraw conversation. Parsing runs as a job so a slow LLM never blocks a request."""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.quote_parser import (
    VENDOR_MESSAGES,
    DocumentError,
    Draft,
    build_draft,
    confirm,
    extract,
    llm_parse,
    load_ctx,
    save_quote,
    summary,
)
from app.channels.base import Outbound
from app.channels.inbound import ROUTERS
from app.channels.simulated import get_channel
from app.db.models import CatalogItem, Message, Quote, Rfq, RfqInvitation, Vendor
from app.domain.quote_text import RFQ_CODE
from app.domain.states import transition
from app.files import get_file_store
from app.jobs.clock import Clock, format_ist
from app.jobs.queue import enqueue, handler
from app.llm.schemas import ParsedQuote

log = logging.getLogger("quotes")

YES, EDIT = "Yes", "Edit"
WITHDRAW_WORDS = {"withdraw", "withdraw quote", "cancel quote", "quote wapas", "quote cancel"}
ACTIVE_INVITES = ("invited", "responded")


def _reply(
    db: Session,
    clock: Clock,
    vendor: Vendor,
    rfq: Rfq | None,
    text: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Free text: always inside the 24 h window, because the vendor just wrote to us."""
    get_channel().send(
        db,
        clock,
        Outbound(
            vendor=vendor,
            text=text,
            payload=payload or {},
            org_id=rfq.builder_org_id if rfq else None,
            rfq_id=rfq.id if rfq else None,
        ),
    )


def open_rfqs(db: Session, vendor: Vendor) -> list[Rfq]:
    return list(
        db.scalars(
            select(Rfq)
            .join(RfqInvitation, RfqInvitation.rfq_id == Rfq.id)
            .where(
                RfqInvitation.vendor_id == vendor.id,
                RfqInvitation.status.in_(ACTIVE_INVITES),
                Rfq.status == "bidding",
            )
            .order_by(Rfq.public_code)
        )
    )


def _latest(db: Session, vendor: Vendor, rfq_id: uuid.UUID | None, status: str) -> Quote | None:
    q = select(Quote).where(Quote.vendor_id == vendor.id, Quote.status == status)
    if rfq_id is not None:
        q = q.where(Quote.rfq_id == rfq_id)
    return db.scalars(q.order_by(Quote.created_at.desc(), Quote.revision.desc())).first()


def route(db: Session, clock: Clock, msg: Message) -> bool:
    """Inbound router. Returns True when the message was handled here."""
    if msg.direction != "in":
        return False
    payload = msg.payload or {}
    if payload.get("form_submission"):
        return True  # handled synchronously by the form endpoint
    vendor = db.get(Vendor, msg.vendor_id)
    assert vendor is not None
    button = payload.get("button")
    rfq = db.get(Rfq, msg.rfq_id) if msg.rfq_id else None

    if button in (YES, EDIT):
        q = _latest(db, vendor, msg.rfq_id, "awaiting_confirmation")
        if q is None:
            return False
        if button == YES:
            confirm(db, clock, q, vendor)
            _reply(
                db,
                clock,
                vendor,
                rfq,
                f"Thank you. Quote {q.public_code} is recorded: {summary(q)}.",
            )
        else:
            transition(
                db,
                clock,
                "quote",
                q,
                "draft_parsed",
                actor=f"vendor:{vendor.id}",
                reason="vendor asked to edit",
            )
            _reply(
                db,
                clock,
                vendor,
                rfq,
                "Please send the corrected rate as a message, or use the quote form.",
            )
        return True

    if isinstance(button, str) and button.startswith("RFQ-") and payload.get("pick_for"):
        chosen = next((r for r in open_rfqs(db, vendor) if r.public_code == button), None)
        if chosen is None:
            return False
        enqueue(
            db,
            "parse_quote",
            clock.now(),
            {"message_id": payload["pick_for"], "rfq_id": str(chosen.id)},
            dedupe_key=f"parse:{payload['pick_for']}:{chosen.id}",
        )
        return True

    if msg.body.strip().lower() in WITHDRAW_WORDS:
        q = _latest(db, vendor, msg.rfq_id, "confirmed")
        if q is None or (
            rfq is not None and rfq.status not in ("bidding", "evaluating", "negotiating")
        ):
            _reply(db, clock, vendor, rfq, "There is no open quote to withdraw.")
            return True
        transition(db, clock, "quote", q, "withdrawn", actor=f"vendor:{vendor.id}")
        _reply(db, clock, vendor, rfq, f"Quote {q.public_code} is withdrawn.")
        return True

    has_file = bool(payload.get("file_ref"))
    looks_like_quote = any(ch.isdigit() for ch in msg.body)
    if has_file or looks_like_quote:
        enqueue(
            db,
            "parse_quote",
            clock.now(),
            {"message_id": str(msg.id)},
            dedupe_key=f"parse:{msg.id}",
        )
        return True
    return False


ROUTERS.append(route)


def _resolve_rfq(
    db: Session,
    clock: Clock,
    msg: Message,
    vendor: Vendor,
    payload: dict[str, Any],
    parsed_code: str | None,
) -> Rfq | None:
    if payload.get("rfq_id"):
        return db.get(Rfq, uuid.UUID(str(payload["rfq_id"])))
    if msg.rfq_id:
        return db.get(Rfq, msg.rfq_id)
    candidates = open_rfqs(db, vendor)
    if parsed_code:
        hit = next((r for r in candidates if r.public_code == parsed_code), None)
        if hit:
            return hit
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        _reply(db, clock, vendor, None, "You have no open RFQ to quote on right now.")
        return None
    items = {c.id: c for c in db.scalars(select(CatalogItem))}
    options = []
    for r in candidates:
        ctx = load_ctx(db, r, clock)
        options.append(
            {"id": r.public_code, "title": f"{r.public_code} · {items[ctx.item.id].name}"}
        )
    _reply(
        db,
        clock,
        vendor,
        None,
        "You have more than one open RFQ. Which one is this quote for?",
        {"list": options, "buttons": [o["id"] for o in options], "pick_for": str(msg.id)},
    )
    return None


@handler("parse_quote")
def parse_quote_job(db: Session, clock: Clock, payload: dict[str, Any]) -> None:
    msg = db.get(Message, uuid.UUID(str(payload["message_id"])))
    if msg is None:
        return
    if db.scalars(select(Quote).where(Quote.source_message_id == msg.id)).first():
        return  # already parsed (job re-run)
    vendor = db.get(Vendor, msg.vendor_id)
    assert vendor is not None
    file_ref = (msg.payload or {}).get("file_ref")
    text, attachment = msg.body or None, None
    if file_ref:
        try:
            text, attachment = extract(get_file_store().read(file_ref))
        except DocumentError as e:
            rfq = db.get(Rfq, msg.rfq_id) if msg.rfq_id else None
            _reply(db, clock, vendor, rfq, VENDOR_MESSAGES[e.code])
            return
        if msg.body and text:
            text = f"{msg.body}\n{text}"

    code_hint = None
    if text and (m := RFQ_CODE.search(text)):
        code_hint = m.group(0)
    rfq = _resolve_rfq(db, clock, msg, vendor, payload, code_hint)
    if rfq is None:
        return
    ctx = load_ctx(db, rfq, clock)
    parsed = llm_parse(ctx, vendor, text, attachment)
    draft = build_draft(parsed, ctx, text, db) if parsed else Draft({}, {}, readable=False)
    if not draft.readable:
        _reply(
            db,
            clock,
            vendor,
            rfq,
            VENDOR_MESSAGES["unreadable"]
            if file_ref
            else "We could not find a rate in your message. Reply like: 380 per bag, GST extra, delivery 5 Oct.",
        )
        return
    source = (
        ("pdf" if attachment is None or attachment.mime_type == "application/pdf" else "photo")
        if file_ref
        else "text"
    )
    q = save_quote(
        db,
        clock,
        ctx,
        vendor,
        draft,
        source=source,
        raw_text=text,
        file_ref=file_ref,
        message_id=msg.id,
    )
    _after_save(db, clock, q, vendor, rfq)


def _after_save(db: Session, clock: Clock, q: Quote, vendor: Vendor, rfq: Rfq) -> None:
    if q.status == "rejected":
        closed = format_ist(rfq.bid_window_closes_at) if rfq.bid_window_closes_at else "earlier"
        _reply(
            db,
            clock,
            vendor,
            rfq,
            f"Thank you. Quotes for {rfq.public_code} closed at {closed}. "
            "Your quote is recorded but cannot be considered this time.",
        )
        return
    notes = []
    if a := q.flags.get("arithmetic_mismatch"):
        notes.append(
            f"the total {a['stated_total']} does not match rate x quantity ({a['rate_x_qty']} for {a['qty']})"
        )
    if p := q.flags.get("possible_typo"):
        notes.append(
            f"this rate is {abs(p['deviation_pct'])}% {'above' if p['deviation_pct'] > 0 else 'below'} the usual rate"
        )
    if c := q.flags.get("converted"):
        notes.append(c)
    if q.flags.get("validity_defaulted"):
        notes.append(f"no validity was stated, so we assumed {q.validity_until:%d %b}")
    if q.status == "awaiting_confirmation":
        if notes:
            _reply(db, clock, vendor, rfq, "Please check: " + "; ".join(notes) + ".")
        get_channel().send(
            db,
            clock,
            Outbound(
                vendor=vendor,
                template="quote_confirm",
                params={"rfq_code": rfq.public_code, "summary": summary(q)},
                payload={"quote_id": str(q.id)},
                org_id=rfq.builder_org_id,
                rfq_id=rfq.id,
            ),
        )
    else:
        _reply(
            db, clock, vendor, rfq, f"Thank you. Quote {q.public_code} is recorded: {summary(q)}."
        )


def form_quote(
    db: Session, clock: Clock, vendor: Vendor, rfq: Rfq, parsed: ParsedQuote, message_id: uuid.UUID
) -> Quote:
    """Structured form: no LLM. Confirmed straight away unless the price looks like a typo."""
    existing = db.scalars(select(Quote).where(Quote.source_message_id == message_id)).first()
    if existing is not None:
        return existing
    ctx = load_ctx(db, rfq, clock)
    draft = build_draft(parsed, ctx, None, db)
    q = save_quote(
        db,
        clock,
        ctx,
        vendor,
        draft,
        source="form",
        raw_text=None,
        file_ref=None,
        message_id=message_id,
    )
    _after_save(db, clock, q, vendor, rfq)
    return q
