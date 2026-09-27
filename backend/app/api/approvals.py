"""Recommendation -> approval -> work orders (PROMPT.md 10.6, Stage 10)."""

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.agents.evaluation import current_recommendation, evaluate
from app.agents.outreach import _ctx
from app.agents.work_orders import (
    AwardError,
    advance_bom,
    cancel_po,
    check_validity,
    issue,
    latest_quote,
    plan,
    po_amounts,
    reinvite,
)
from app.api.deps import BizClock, Builder, Db, org_id, require
from app.api.rfqs import comparison_view
from app.channels.base import Outbound
from app.channels.simulated import get_channel, window_open
from app.db.audit import audit
from app.db.models import (
    Approval,
    BomLine,
    Delivery,
    Invoice,
    NegotiationThread,
    Rfq,
    User,
    Vendor,
    WorkOrder,
)
from app.db.tenancy import get_owned
from app.domain.money import format_inr
from app.domain.states import InvalidTransition, transition
from app.domain.units import format_qty
from app.files import get_file_store
from app.jobs.clock import format_ist, to_ist
from app.jobs.queue import enqueue

router = APIRouter(tags=["approvals"])

Approver = Annotated[User, Depends(require("award.approve"))]


def _wo_out(db: Session, wo: WorkOrder) -> dict[str, Any]:
    vendor = db.get(Vendor, wo.vendor_id)
    rfq = db.get(Rfq, wo.rfq_id)
    _, _, item, _, _ = _ctx(db, rfq) if rfq else (None, None, None, None, None)
    return {
        "id": str(wo.id),
        "code": wo.public_code,
        "rfq_id": str(wo.rfq_id),
        "rfq_code": rfq.public_code if rfq else None,
        "vendor": vendor.display_name if vendor else None,
        "item": item.name if item else None,
        "unit": item.canonical_unit if item else None,
        "qty_milli": wo.qty_milli,
        "qty_display": format_qty(wo.qty_milli, item.canonical_unit) if item else None,
        "unit_price_paise": wo.unit_price_paise,
        "subtotal_paise": wo.subtotal_paise,
        "gst_paise": wo.gst_paise,
        "freight_paise": wo.freight_paise,
        "total_paise": wo.total_paise,
        "delivery_date": wo.delivery_date,
        "status": wo.status,
        "confirm_by": wo.confirm_by,
        "confirmed_at": wo.confirmed_at,
        "shortfall_note": wo.shortfall_note,
        "version": wo.version,
    }


def _already_approved(db: Session, rfq: Rfq) -> str:
    a = db.scalars(
        select(Approval)
        .where(Approval.rfq_id == rfq.id, Approval.decision == "approved")
        .order_by(Approval.created_at.desc())
    ).first()
    if a is None:
        return "This recommendation changed after you opened it. Reload and check again."
    who = db.get(User, a.approver_id)
    return (
        f"Already approved by {who.name if who else 'another user'} at {to_ist(a.created_at):%H:%M}"
    )


def _result(
    db: Session, rfq: Rfq, approval: Approval | None, message: str | None = None
) -> dict[str, Any]:
    orders = db.scalars(
        select(WorkOrder).where(WorkOrder.rfq_id == rfq.id).order_by(WorkOrder.public_code)
    )
    return {
        "status": rfq.status,
        "decision": approval.decision if approval else None,
        "message": message,
        "work_orders": [_wo_out(db, w) for w in orders],
        "version": rfq.version,
    }


class ApproveIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=80)
    choice: str = Field(default="l1", max_length=40)  # "l1" | "split" | vendor id
    override_above_max: bool = False
    version: int  # the RFQ version the approver was looking at


def _limit(user: User, cfg: dict[str, Any]) -> int | None:
    if user.role == "owner":
        return None
    return (
        user.approval_limit_paise
        if user.approval_limit_paise is not None
        else cfg["approval_limits_paise"].get(user.role)
    )


@router.post("/rfqs/{rfq_id}/approve")
def approve(
    rfq_id: uuid.UUID, body: ApproveIn, user: Approver, db: Db, clock: BizClock
) -> dict[str, Any]:
    prior = db.scalars(
        select(Approval).where(Approval.idempotency_key == body.idempotency_key)
    ).one_or_none()
    if prior is not None:  # double click / retry: same answer, nothing new
        if prior.builder_org_id != org_id(user):
            raise HTTPException(409, "Duplicate request reference")
        rfq = db.get(Rfq, prior.rfq_id)
        assert rfq is not None
        return _result(db, rfq, prior)
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if rfq.status in ("awarded", "closed"):
        raise HTTPException(409, _already_approved(db, rfq))
    if rfq.status != "awaiting_approval":
        raise HTTPException(409, f"{rfq.public_code} is {rfq.status}; nothing to approve yet")
    if body.version != rfq.version:
        raise HTTPException(409, _already_approved(db, rfq))
    try:
        allocs = plan(db, rfq, body.choice, body.override_above_max)
        check_validity(db, clock, rfq, allocs)
    except AwardError as e:
        raise HTTPException(409, str(e)) from None

    org, line, item, _, cfg = _ctx(db, rfq)
    rec = current_recommendation(db, rfq.id)
    supply = {r["vendor_id"]: r["supply_milli"] for r in (rec.ranked if rec else [])}
    value = 0
    for a in allocs:
        q = latest_quote(db, rfq.id, a.vendor_id)
        assert q is not None
        value += po_amounts(
            q, a.qty_milli, supply.get(str(a.vendor_id)) or a.qty_milli, item.default_gst_bp
        )["total_paise"]
    limit = _limit(user, cfg)
    if limit is not None and value > limit:
        routed = Approval(builder_org_id=rfq.builder_org_id, rfq_id=rfq.id, approver_id=user.id, decision="routed_to_owner",
                     idempotency_key=body.idempotency_key,
                     note=f"{format_inr(value)} is above {user.name}'s limit of {format_inr(limit)}")  # fmt: skip
        db.add(routed)
        audit(db, clock, actor=f"user:{user.id}", action="approval.routed_to_owner", entity="rfq", entity_id=rfq.id,
              org_id=rfq.builder_org_id, after={"value": value, "limit": limit, "choice": body.choice})  # fmt: skip
        db.commit()
        return _result(
            db,
            rfq,
            routed,
            f"{format_inr(value)} is above your approval limit of {format_inr(limit)}. Sent to the owner to approve.",
        )

    approval = Approval(builder_org_id=rfq.builder_org_id, rfq_id=rfq.id, approver_id=user.id, decision="approved",
                        idempotency_key=body.idempotency_key, note=body.choice)  # fmt: skip
    db.add(approval)
    try:
        transition(
            db,
            clock,
            "rfq",
            rfq,
            "awarded",
            actor=f"user:{user.id}",
            reason=f"approved ({body.choice})",
        )
        issue(db, clock, rfq, allocs, user)
        line_ = db.get(BomLine, rfq.bom_line_id)
        assert line_ is not None
        advance_bom(db, clock, line_.bom_id, f"user:{user.id}")
        db.commit()
    except AwardError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from None
    except StaleDataError:  # someone else approved between our read and write
        db.rollback()
        rfq = db.get(Rfq, rfq_id)
        assert rfq is not None
        raise HTTPException(409, _already_approved(db, rfq)) from None
    return _result(db, rfq, approval, "Approved. Work orders sent to the vendor for confirmation.")


@router.post("/rfqs/{rfq_id}/compare-again")
def compare_again(rfq_id: uuid.UUID, user: Approver, db: Db, clock: BizClock) -> dict[str, Any]:
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if rfq.status != "awaiting_approval":
        raise HTTPException(409, f"{rfq.public_code} is {rfq.status}")
    evaluate(db, clock, rfq, actor=f"user:{user.id}")
    db.commit()
    return comparison_view(db, rfq)


class ReviewIn(BaseModel):
    note: str = Field(default="", max_length=500)


@router.post("/rfqs/{rfq_id}/review")
def send_for_review(
    rfq_id: uuid.UUID, body: ReviewIn, user: Builder, db: Db, clock: BizClock
) -> dict[str, Any]:
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if rfq.status != "awaiting_approval":
        raise HTTPException(409, f"{rfq.public_code} is {rfq.status}")
    a = Approval(builder_org_id=rfq.builder_org_id, rfq_id=rfq.id, approver_id=user.id, decision="review",
                 idempotency_key=uuid.uuid4().hex, note=body.note or None)  # fmt: skip
    db.add(a)
    audit(db, clock, actor=f"user:{user.id}", action="approval.review", entity="rfq", entity_id=rfq.id,
          org_id=rfq.builder_org_id, after={"note": body.note})  # fmt: skip
    db.commit()
    return {"ok": True}


class ReconfirmIn(BaseModel):
    vendor_id: uuid.UUID


@router.post("/rfqs/{rfq_id}/reconfirm")
def reconfirm(
    rfq_id: uuid.UUID, body: ReconfirmIn, user: Approver, db: Db, clock: BizClock
) -> dict[str, Any]:
    """An offer expired before approval: ask the vendor to confirm or re-quote."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    vendor = db.get(Vendor, body.vendor_id)
    if vendor is None or latest_quote(db, rfq.id, vendor.id) is None:
        raise HTTPException(404, "Not found")
    text = (f"Your quotation for {rfq.public_code} has passed its validity date. Is it still open? "
            "Please reply with your current rate and how long it is valid, or use the quote form.")  # fmt: skip
    msg = Outbound(
        vendor=vendor,
        org_id=rfq.builder_org_id,
        rfq_id=rfq.id,
        payload={"form": {"type": "quote_form", "rfq_id": str(rfq.id)}},
    )
    if window_open(db, vendor.id, clock.now()):
        msg.text = text
    else:
        msg.template, msg.params = "counter_offer", {"rfq_code": rfq.public_code, "message": text}
    get_channel().send(db, clock, msg)
    if rfq.status == "awaiting_approval":
        # Re-open bidding briefly so the vendor's fresh quote is accepted, then re-score.
        _, _, _, _, cfg = _ctx(db, rfq)
        transition(
            db,
            clock,
            "rfq",
            rfq,
            "bidding",
            actor=f"user:{user.id}",
            reason=f"asked {vendor.display_name} to reconfirm",
        )
        rfq.bid_window_closes_at = clock.now() + timedelta(hours=int(cfg["bid_window_hours"]))
        rfq.window_extended = True  # no automatic extension on this short window
        enqueue(
            db,
            "bid_close",
            rfq.bid_window_closes_at,
            {"rfq_id": str(rfq.id)},
            dedupe_key=f"close:{rfq.id}:reconfirm:{rfq.version}",
        )
    db.commit()
    return {"ok": True, "status": rfq.status}


@router.post("/rfqs/{rfq_id}/rebid")
def rebid(rfq_id: uuid.UUID, user: Approver, db: Db, clock: BizClock) -> dict[str, Any]:
    """The award fell through and no runner-up is still valid: bid again."""
    rfq = get_owned(db, Rfq, rfq_id, org_id(user))
    if rfq.status != "awaiting_approval":
        raise HTTPException(409, f"{rfq.public_code} is {rfq.status}")
    reinvite(db, clock, rfq, f"user:{user.id}")
    db.commit()
    return {"ok": True, "status": rfq.status}


# --- work orders -------------------------------------------------------------------------


@router.get("/work-orders")
def list_work_orders(user: Builder, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(WorkOrder)
        .where(WorkOrder.builder_org_id == org_id(user))
        .order_by(WorkOrder.created_at.desc())
    )
    return [_wo_out(db, w) for w in rows]


@router.get("/work-orders/{wo_id}")
def get_work_order(wo_id: uuid.UUID, user: Builder, db: Db) -> dict[str, Any]:
    wo = get_owned(db, WorkOrder, wo_id, org_id(user))
    out = _wo_out(db, wo)
    out["deliveries"] = [
        {"id": str(d.id), "code": d.public_code, "status": d.status, "dispatched_at": d.dispatched_at,
         "vehicle_no": d.vehicle_no, "dispatched_qty_milli": d.dispatched_qty_milli,
         "received_qty_milli": d.received_qty_milli, "received_at": d.received_at, "flags": d.flags,
         "has_photo": d.received_photo_ref is not None}
        for d in db.scalars(select(Delivery).where(Delivery.work_order_id == wo.id).order_by(Delivery.seq))
    ]  # fmt: skip
    out["invoices"] = [
        {"id": str(i.id), "invoice_no": i.invoice_no, "amount_paise": i.amount_paise, "unit_price_paise": i.unit_price_paise,
         "status": i.status, "mismatch_flags": i.mismatch_flags, "has_file": i.file_ref is not None}
        for i in db.scalars(select(Invoice).where(Invoice.work_order_id == wo.id).order_by(Invoice.created_at))
    ]  # fmt: skip
    out["confirm_by_display"] = format_ist(wo.confirm_by) if wo.confirm_by else None
    return out


@router.get("/work-orders/{wo_id}/pdf")
def work_order_pdf(wo_id: uuid.UUID, user: Builder, db: Db) -> Response:
    wo = get_owned(db, WorkOrder, wo_id, org_id(user))
    if not wo.pdf_ref:
        raise HTTPException(404, "Not found")
    return Response(get_file_store().read(wo.pdf_ref), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{wo.public_code}.pdf"'})  # fmt: skip


@router.post("/work-orders/{wo_id}/cancel")
def cancel_work_order(wo_id: uuid.UUID, user: Approver, db: Db, clock: BizClock) -> dict[str, Any]:
    wo = get_owned(db, WorkOrder, wo_id, org_id(user))
    try:
        cancel_po(db, clock, wo, f"user:{user.id}", "cancelled by the buyer")
    except InvalidTransition:
        raise HTTPException(
            409, f"A {wo.status.replace('_', ' ')} work order cannot be cancelled"
        ) from None
    db.commit()
    return _wo_out(db, wo)


# --- dashboard ----------------------------------------------------------------------------


@router.get("/dashboard/actions")
def actions(user: Builder, db: Db) -> dict[str, Any]:
    org = org_id(user)
    awaiting = db.scalars(
        select(Rfq).where(Rfq.builder_org_id == org, Rfq.status == "awaiting_approval")
    ).all()
    routed = {
        a.rfq_id
        for a in db.scalars(
            select(Approval).where(
                Approval.builder_org_id == org, Approval.decision == "routed_to_owner"
            )
        )
    }
    handoffs = db.scalars(
        select(NegotiationThread).where(
            NegotiationThread.builder_org_id == org, NegotiationThread.state == "needs_human"
        )
    ).all()
    pos = db.scalars(
        select(WorkOrder).where(WorkOrder.builder_org_id == org, WorkOrder.status == "issued")
    ).all()
    names = {v.id: v.display_name for v in db.scalars(select(Vendor))}
    return {
        "approvals": [
            {"rfq_id": str(r.id), "code": r.public_code, "routed_to_owner": r.id in routed,
             "runner_up": bool(r.match_report.get("runner_up")), "single_quote": bool(r.match_report.get("single_quote"))}
            for r in awaiting
        ],
        "handoffs": [
            {"rfq_id": str(t.rfq_id), "thread": t.public_code, "vendor": names.get(t.vendor_id), "reason": t.handoff_reason}
            for t in handoffs
        ],
        "pending_confirmation": [
            {"id": str(w.id), "code": w.public_code, "vendor": names.get(w.vendor_id), "confirm_by": w.confirm_by}
            for w in pos
        ],
    }  # fmt: skip
