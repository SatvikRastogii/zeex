"""RFQ views and shortlist editing (matching review)."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.evaluation import current_recommendation, evaluate
from app.agents.match_runner import MATCHABLE, load_context, run_matching
from app.agents.matching import MAX_INVITES, exclusion, score
from app.agents.negotiation import maybe_complete, take_over, threads_of
from app.agents.outreach import OutreachError, send_rfqs
from app.api.deps import BizClock, Builder, Db, org_id, require
from app.channels.base import Outbound
from app.channels.simulated import get_channel, window_open
from app.db.audit import audit
from app.db.models import (
    Bom,
    BomLine,
    BuilderOrg,
    CatalogItem,
    Message,
    NegotiationThread,
    Quote,
    Rfq,
    RfqInvitation,
    Site,
    User,
    Vendor,
)
from app.db.tenancy import get_owned
from app.domain.settings import org_settings
from app.domain.states import transition
from app.domain.units import format_qty
from app.files import get_file_store

router = APIRouter(prefix="/rfqs", tags=["rfqs"])

Editor = Annotated[User, Depends(require("shortlist.edit"))]

ACTIVE_INVITES = ("proposed", "invited", "responded")


def rfq_view(db: Session, rfq: Rfq) -> dict[str, Any]:
    line = db.get(BomLine, rfq.bom_line_id)
    assert line is not None
    bom = db.get(Bom, line.bom_id)
    item = db.get(CatalogItem, line.catalog_item_id) if line.catalog_item_id else None
    assert bom is not None and item is not None
    site = db.get(Site, bom.site_id)
    assert site is not None
    vendors = {
        v.id: v
        for v in db.scalars(
            select(Vendor).join(RfqInvitation).where(RfqInvitation.rfq_id == rfq.id)
        )
    }
    invites = db.scalars(
        select(RfqInvitation)
        .where(RfqInvitation.rfq_id == rfq.id)
        .order_by(RfqInvitation.match_score.desc())
    )
    return {
        "id": str(rfq.id),
        "code": rfq.public_code,
        "status": rfq.status,
        "revision": rfq.revision,
        "stale": rfq.stale,
        "bid_window_opens_at": rfq.bid_window_opens_at,
        "bid_window_closes_at": rfq.bid_window_closes_at,
        "bom": {"id": str(bom.id), "code": bom.public_code, "status": bom.status},
        "site": {"name": site.name, "area": site.area, "pincode": site.pincode},
        "line": {
            "line_no": line.line_no,
            "item": {
                "id": str(item.id),
                "code": item.code,
                "name": item.name,
                "canonical_unit": item.canonical_unit,
            },
            "qty_milli": line.qty_canonical_milli,
            "qty_display": format_qty(line.qty_canonical_milli, item.canonical_unit),
            "needed_by": line.needed_by.isoformat(),
            "partial_allowed": line.partial_allowed,
        },
        "match_report": rfq.match_report,
        "shortlist": [
            {
                "vendor_id": str(i.vendor_id),
                "vendor": vendors[i.vendor_id].display_name,
                "score": i.match_score,
                "reason": i.match_reasons.get("summary", ""),
                "parts": i.match_reasons.get("parts", {}),
                "added_by_builder": bool(i.match_reasons.get("added_by_builder")),
                "status": i.status,
                "invited_at": i.invited_at,
                "reminded_at": i.reminded_at,
            }
            for i in invites
        ],
    }


@router.get("/{rfq_id}")
def get_rfq(rfq_id: uuid.UUID, user: Builder, db: Db) -> dict[str, Any]:
    return rfq_view(db, get_owned(db, Rfq, rfq_id, org_id(user)))


class MatchIn(BaseModel):
    extra_radius_km: int = Field(default=0, ge=0, le=100)
    allow_partial: bool = False


def _editable(rfq: Rfq) -> None:
    if rfq.status not in MATCHABLE:
        raise HTTPException(
            409, f"{rfq.public_code} is {rfq.status}; the shortlist is fixed once RFQs are sent"
        )


@router.post("/{rfq_id}/match")
def rematch(
    rfq_id: uuid.UUID, body: MatchIn, user: Editor, db: Db, clock: BizClock
) -> dict[str, Any]:
    """Re-run matching, optionally with a wider radius or partial supply allowed."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    _editable(rfq)
    if body.allow_partial:
        line = db.get(BomLine, rfq.bom_line_id)
        assert line is not None
        line.partial_allowed = True
    run_matching(db, clock, rfq, actor=f"user:{user.id}", extra_radius_km=body.extra_radius_km)
    db.commit()
    return rfq_view(db, rfq)


@router.get("/{rfq_id}/candidates")
def candidates(rfq_id: uuid.UUID, user: Editor, db: Db) -> list[dict[str, Any]]:
    """Linked vendors who sell the item and are not on the shortlist, with eligibility."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    line_ctx, vendors, _, _ = load_context(db, rfq)
    extra = int(rfq.match_report.get("extra_radius_km", 0))
    on_list = set(
        db.scalars(
            select(RfqInvitation.vendor_id).where(
                RfqInvitation.rfq_id == rfq.id, RfqInvitation.status.in_(ACTIVE_INVITES)
            )
        )
    )
    out = []
    for v in vendors:
        if not v.linked or v.id in on_list:
            continue
        why = exclusion(v, line_ctx, extra)
        out.append(
            {
                "vendor_id": str(v.id),
                "vendor": v.name,
                "eligible": why is None,
                "reason": why or score(v, line_ctx, extra).reason,
            }
        )
    return sorted(out, key=lambda c: (not c["eligible"], c["vendor"]))


class ShortlistIn(BaseModel):
    vendor_id: uuid.UUID
    action: Literal["add", "remove"]


@router.post("/{rfq_id}/shortlist")
def edit_shortlist(
    rfq_id: uuid.UUID, body: ShortlistIn, user: Editor, db: Db, clock: BizClock
) -> dict[str, Any]:
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    _editable(rfq)
    inv = db.scalars(
        select(RfqInvitation).where(
            RfqInvitation.rfq_id == rfq.id, RfqInvitation.vendor_id == body.vendor_id
        )
    ).one_or_none()

    if body.action == "remove":
        if inv is None or inv.status != "proposed":
            raise HTTPException(404, "Vendor is not on the shortlist")
        inv.status = "removed"
    else:
        line_ctx, vendors, _, _ = load_context(db, rfq)
        v = next((x for x in vendors if x.id == body.vendor_id and x.linked), None)
        if v is None:
            raise HTTPException(404, "Vendor not found")
        extra = int(rfq.match_report.get("extra_radius_km", 0))
        why = exclusion(v, line_ctx, extra)
        if why:
            raise HTTPException(422, f"{v.name} cannot be invited: {why}")
        active = db.scalars(
            select(RfqInvitation).where(
                RfqInvitation.rfq_id == rfq.id, RfqInvitation.status == "proposed"
            )
        ).all()
        if len(active) >= MAX_INVITES:
            raise HTTPException(422, f"At most {MAX_INVITES} vendors per RFQ")
        s = score(v, line_ctx, extra)
        reasons = {"summary": s.reason, "parts": s.parts, "added_by_builder": True}
        if inv is None:
            db.add(
                RfqInvitation(
                    builder_org_id=rfq.builder_org_id,
                    rfq_id=rfq.id,
                    vendor_id=v.id,
                    match_score=s.score,
                    match_reasons=reasons,
                    status="proposed",
                )
            )
        elif inv.status == "removed":
            inv.status = "proposed"
        if rfq.status == "no_vendors_matched":
            transition(
                db, clock, "rfq", rfq, "matching", actor=f"user:{user.id}", reason="vendor added"
            )
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action=f"shortlist.{body.action}",
        entity="rfq",
        entity_id=rfq.id,
        org_id=rfq.builder_org_id,
        after={"vendor_id": str(body.vendor_id)},
    )
    db.commit()
    return rfq_view(db, rfq)


@router.post("/{rfq_id}/send")
def send(rfq_id: uuid.UUID, user: Editor, db: Db, clock: BizClock) -> dict[str, Any]:
    """Open the bid window and queue invitations (sent within working hours)."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if rfq.status in {"invited", "bidding"}:
        return rfq_view(db, rfq)  # already sent: idempotent
    try:
        send_rfqs(db, clock, rfq, actor=f"user:{user.id}")
    except OutreachError as e:
        raise HTTPException(409, str(e)) from None
    db.commit()
    return rfq_view(db, rfq)


def quote_out(q: Quote, vendor_name: str) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "code": q.public_code,
        "vendor_id": str(q.vendor_id),
        "vendor": vendor_name,
        "revision": q.revision,
        "source": q.source,
        "status": q.status,
        "unit_price_paise": q.unit_price_paise,
        "price_unit": q.price_unit,
        "price_per_canonical_paise": q.price_per_canonical_paise,
        "gst_included": q.gst_included,
        "gst_bp": q.gst_bp,
        "freight_paise": q.freight_paise,
        "freight_included": q.freight_included,
        "unloading_paise": q.unloading_paise,
        "delivery_date": q.delivery_date,
        "validity_until": q.validity_until,
        "payment_terms_days": q.payment_terms_days,
        "brand": q.brand,
        "qty_offered_milli": q.qty_offered_milli,
        "stated_total_paise": q.stated_total_paise,
        "parse_confidence": q.parse_confidence,
        "flags": q.flags,
        "has_file": q.raw_file_ref is not None,
        "raw_text": q.raw_text if q.source in ("text", "pdf") else None,
        "received_at": q.received_at,
        "confirmed_at": q.confirmed_by_vendor_at,
    }


@router.get("/{rfq_id}/quotes")
def list_quotes(rfq_id: uuid.UUID, user: Builder, db: Db) -> list[dict[str, Any]]:
    """Every quote and revision for the RFQ, newest first per vendor."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    names = {v.id: v.display_name for v in db.scalars(select(Vendor))}
    rows = db.scalars(
        select(Quote).where(Quote.rfq_id == rfq.id).order_by(Quote.vendor_id, Quote.revision.desc())
    )
    return [quote_out(q, names.get(q.vendor_id, "")) for q in rows]


quotes_router = APIRouter(prefix="/quotes", tags=["quotes"])

MEDIA = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@quotes_router.get("/{quote_id}/file")
def quote_file(quote_id: uuid.UUID, user: Builder, db: Db) -> Response:
    """The vendor's original document, shown next to the parsed values."""
    q = get_owned(db, Quote, quote_id, org_id(user))
    if not q.raw_file_ref:
        raise HTTPException(404, "Not found")
    ext = q.raw_file_ref[q.raw_file_ref.rfind(".") :].lower()
    return Response(
        get_file_store().read(q.raw_file_ref),
        media_type=MEDIA.get(ext, "application/octet-stream"),
        headers={
            "Content-Disposition": f'inline; filename="{q.public_code}{ext}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def comparison_view(db: Session, rfq: Rfq) -> dict[str, Any]:
    rec = current_recommendation(db, rfq.id)
    org = db.get(BuilderOrg, rfq.builder_org_id)
    assert org is not None
    cfg = org_settings(org.settings)
    return {
        "rfq_id": str(rfq.id),
        "status": rfq.status,
        "weights": cfg["weights"],
        "gst_mode": cfg["gst_mode"],
        "window_extended": rfq.window_extended,
        "single_quote": bool(rfq.match_report.get("single_quote")),
        "target_price_paise": rfq.target_price_paise,  # builder-only view
        "max_price_paise": rfq.max_price_paise,
        "recommendation": None
        if rec is None
        else {
            "generated_at": rec.generated_at,
            "ranked": rec.ranked,
            "l1_vendor_id": str(rec.l1_vendor_id) if rec.l1_vendor_id else None,
            "lowest_price_vendor_id": str(rec.lowest_price_vendor_id)
            if rec.lowest_price_vendor_id
            else None,
            "split_proposal": rec.split_proposal,
        },
    }


@router.get("/{rfq_id}/comparison")
def comparison(rfq_id: uuid.UUID, user: Builder, db: Db) -> dict[str, Any]:
    return comparison_view(db, get_owned(db, Rfq, rfq_id, org_id(user)))


REEVALUABLE = {"evaluating", "negotiating", "awaiting_approval"}


@router.post("/{rfq_id}/evaluate")
def reevaluate(rfq_id: uuid.UUID, user: Editor, db: Db, clock: BizClock) -> dict[str, Any]:
    """Score the confirmed quotes again (for example after changing weights)."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if rfq.status not in REEVALUABLE:
        raise HTTPException(
            409,
            f"{rfq.public_code} is {rfq.status}; quotes are compared after the bid window closes",
        )
    evaluate(db, clock, rfq, actor=f"user:{user.id}")
    db.commit()
    return comparison_view(db, rfq)


class LimitsIn(BaseModel):
    """Private to the builder: never shown to vendors, never sent to the LLM."""

    target_price_paise: int | None = Field(default=None, gt=0)
    max_price_paise: int | None = Field(default=None, gt=0)


@router.put("/{rfq_id}/limits")
def set_limits(
    rfq_id: uuid.UUID, body: LimitsIn, user: Editor, db: Db, clock: BizClock
) -> dict[str, Any]:
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if (
        body.target_price_paise
        and body.max_price_paise
        and body.target_price_paise > body.max_price_paise
    ):
        raise HTTPException(422, "Target price cannot be above the maximum price")
    rfq.target_price_paise, rfq.max_price_paise = body.target_price_paise, body.max_price_paise
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action="rfq.limits",
        entity="rfq",
        entity_id=rfq.id,
        org_id=rfq.builder_org_id,
        after={
            "target_set": body.target_price_paise is not None,
            "max_set": body.max_price_paise is not None,
        },
    )
    if rfq.status in REEVALUABLE:
        evaluate(db, clock, rfq, actor=f"user:{user.id}")
    db.commit()
    return comparison_view(db, rfq)


def _thread_out(db: Session, t: NegotiationThread, vendor_name: str) -> dict[str, Any]:
    msgs = db.scalars(
        select(Message)
        .where(Message.thread_id == t.id)
        .order_by(Message.sent_at, Message.created_at)
    )
    return {
        "id": str(t.id),
        "code": t.public_code,
        "vendor_id": str(t.vendor_id),
        "vendor": vendor_name,
        "state": t.state,
        "round": t.round,
        "opening_offer_paise": t.opening_offer_paise,
        "current_offer_paise": t.current_offer_paise,
        "last_counter_paise": t.last_counter_paise,
        "deadline_at": t.deadline_at,
        "reply_due_at": t.reply_due_at,
        "handoff_reason": t.handoff_reason,
        "taken_over": t.handed_to_human_by is not None,
        "messages": [
            {
                "id": str(m.id),
                "direction": m.direction,
                "body": m.body,
                "sent_at": m.sent_at,
                "status": m.status,
                "stale": bool((m.payload or {}).get("stale")),
                "by_human": bool((m.payload or {}).get("by_user")),
            }
            for m in msgs
        ],
    }


@router.get("/{rfq_id}/negotiations")
def negotiations(rfq_id: uuid.UUID, user: Builder, db: Db) -> list[dict[str, Any]]:
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    names = {v.id: v.display_name for v in db.scalars(select(Vendor))}
    return [_thread_out(db, t, names.get(t.vendor_id, "")) for t in threads_of(db, rfq.id)]


neg_router = APIRouter(prefix="/negotiations", tags=["negotiation"])
TakeOver = Annotated[User, Depends(require("negotiation.take_over"))]


@neg_router.post("/{thread_id}/take-over")
def take_over_thread(
    thread_id: uuid.UUID, user: TakeOver, db: Db, clock: BizClock
) -> dict[str, Any]:
    t = get_owned(db, NegotiationThread, thread_id, org_id(user))
    if t.handed_to_human_by is None:
        if t.state in ("closed",):
            raise HTTPException(409, "This negotiation is already closed")
        t.handed_to_human_by = user.id
        if t.state != "needs_human":
            take_over(db, clock, t, user.id, user.name)
        else:
            t.handoff_reason = f"{t.handoff_reason}; taken over by {user.name}"
        db.commit()
    vendor = db.get(Vendor, t.vendor_id)
    return _thread_out(db, t, vendor.display_name if vendor else "")


class ManualMessage(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@neg_router.post("/{thread_id}/message")
def manual_message(
    thread_id: uuid.UUID, body: ManualMessage, user: TakeOver, db: Db, clock: BizClock
) -> dict[str, Any]:
    """Only after taking over: the builder writes to the vendor directly."""
    t = get_owned(db, NegotiationThread, thread_id, org_id(user))
    if t.handed_to_human_by is None:
        raise HTTPException(409, "Take over the negotiation first")
    vendor = db.get(Vendor, t.vendor_id)
    rfq = db.get(Rfq, t.rfq_id)
    assert vendor is not None and rfq is not None
    msg = Outbound(
        vendor=vendor,
        org_id=t.builder_org_id,
        rfq_id=t.rfq_id,
        thread_id=t.id,
        payload={"by_user": str(user.id)},
    )
    if window_open(db, vendor.id, clock.now()):
        msg.text = body.text
    else:
        msg.template, msg.params = (
            "counter_offer",
            {"rfq_code": rfq.public_code, "message": body.text},
        )
    get_channel().send(db, clock, msg)
    db.commit()
    return _thread_out(db, t, vendor.display_name)


@neg_router.post("/{thread_id}/close")
def close_thread(thread_id: uuid.UUID, user: TakeOver, db: Db, clock: BizClock) -> dict[str, Any]:
    """The builder ends a handed-over thread; the vendor's last confirmed offer stands."""
    t = get_owned(db, NegotiationThread, thread_id, org_id(user))
    if t.state != "closed":
        transition(
            db,
            clock,
            "negotiation",
            t,
            "closed",
            actor=f"user:{user.id}",
            field="state",
            reason="closed by builder",
        )
        maybe_complete(db, clock, t.rfq_id)
        db.commit()
    vendor = db.get(Vendor, t.vendor_id)
    return _thread_out(db, t, vendor.display_name if vendor else "")
