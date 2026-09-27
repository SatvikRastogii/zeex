"""Delivery and closure (PROMPT.md 10.7): dispatch, receipt, invoice check, closure,
vendor ratings and price history. Plain code."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.outreach import _ctx
from app.channels.base import Outbound
from app.channels.simulated import get_channel
from app.db.audit import audit
from app.db.ids import delivery_code
from app.db.models import (
    Bom,
    BomLine,
    Delivery,
    Invoice,
    PriceHistory,
    Quote,
    Rfq,
    RfqInvitation,
    Vendor,
    WorkOrder,
)
from app.domain.money import div_round_half_up, format_inr
from app.domain.states import can_transition, transition
from app.domain.units import format_qty
from app.jobs.clock import Clock, ist_today, to_ist

MILLI = 1000
TOLERANCE_PAISE = 100  # ₹1
BP = 10_000


class DeliveryError(Exception):
    """Shown to the user as is."""


def received_milli(db: Session, wo: WorkOrder) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Delivery.received_qty_milli), 0)).where(
                Delivery.work_order_id == wo.id
            )
        )
        or 0
    )


def in_transit_milli(db: Session, wo: WorkOrder) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Delivery.dispatched_qty_milli), 0)).where(
                Delivery.work_order_id == wo.id, Delivery.status == "dispatched"
            )
        )
        or 0
    )


def _unit(db: Session, wo: WorkOrder) -> str:
    rfq = db.get(Rfq, wo.rfq_id)
    assert rfq is not None
    _, _, item, _, _ = _ctx(db, rfq)
    return item.canonical_unit


def dispatch(
    db: Session,
    clock: Clock,
    wo: WorkOrder,
    vehicle_no: str,
    qty_milli: int | None,
    client_ref: str,
) -> Delivery:
    existing = db.scalars(select(Delivery).where(Delivery.client_ref == client_ref)).one_or_none()
    if existing is not None:
        return existing
    if wo.status not in ("vendor_confirmed", "in_delivery"):
        raise DeliveryError(
            f"Work order {wo.public_code} is {wo.status.replace('_', ' ')}; it cannot be dispatched"
        )
    remaining = wo.qty_milli - received_milli(db, wo) - in_transit_milli(db, wo)
    if remaining <= 0:
        raise DeliveryError("The full quantity has already been dispatched")
    qty = qty_milli if qty_milli is not None else remaining
    if qty <= 0:
        raise DeliveryError("Quantity must be more than zero")
    if qty < remaining and not wo.partial_allowed:
        raise DeliveryError(
            f"This work order does not allow partial deliveries: dispatch the full {format_qty(remaining, _unit(db, wo))}"
        )
    seq = (
        db.scalar(select(func.max(Delivery.seq)).where(Delivery.work_order_id == wo.id)) or 0
    ) + 1
    d = Delivery(
        builder_org_id=wo.builder_org_id, public_code=delivery_code(wo.public_code, seq), work_order_id=wo.id, seq=seq,
        dispatched_at=clock.now(), vehicle_no=vehicle_no.strip().upper()[:20], dispatched_qty_milli=qty,
        status="dispatched", client_ref=client_ref,
        flags={"over_dispatch_milli": qty - remaining} if qty > remaining else {},
    )  # fmt: skip
    db.add(d)
    if wo.status == "vendor_confirmed":
        transition(db, clock, "work_order", wo, "in_delivery", actor=f"vendor:{wo.vendor_id}")
    db.flush()
    audit(db, clock, actor=f"vendor:{wo.vendor_id}", action="delivery.dispatched", entity="delivery", entity_id=d.id,
          org_id=wo.builder_org_id, after={"code": d.public_code, "qty_milli": qty, "vehicle": d.vehicle_no})  # fmt: skip
    return d


def receive(
    db: Session, clock: Clock, wo: WorkOrder, qty_milli: int, photo_ref: str, user_id: uuid.UUID
) -> Delivery:
    """Site confirms what arrived. Over-delivery: only the ordered quantity is accepted.
    Short: flagged; the PO stays open for the rest (or the builder closes it with a note)."""
    d = db.scalars(
        select(Delivery)
        .where(Delivery.work_order_id == wo.id, Delivery.status == "dispatched")
        .order_by(Delivery.seq)
    ).first()
    if d is None:
        raise DeliveryError(
            "Nothing has been dispatched on this work order yet, so there is nothing to receive"
        )
    if qty_milli < 0:
        raise DeliveryError("Quantity cannot be negative")
    unit = _unit(db, wo)
    outstanding = wo.qty_milli - received_milli(db, wo)
    flags: dict[str, Any] = dict(d.flags)
    accepted = min(qty_milli, outstanding)
    if qty_milli > outstanding:
        flags["over_delivery"] = (
            f"{format_qty(qty_milli - outstanding, unit)} more than ordered arrived; only the ordered quantity is accepted"
        )
    if qty_milli < (d.dispatched_qty_milli or 0):
        flags["short_delivery"] = (
            f"{format_qty((d.dispatched_qty_milli or 0) - qty_milli, unit)} short of what was dispatched"
        )
    rfq = db.get(Rfq, wo.rfq_id)
    assert rfq is not None
    _, line, _, _, _ = _ctx(db, rfq)
    today = ist_today(clock)
    if today > line.needed_by:
        flags["late_days"] = (today - line.needed_by).days
    d.received_qty_milli, d.received_photo_ref, d.received_by, d.received_at = (
        accepted,
        photo_ref,
        user_id,
        clock.now(),
    )
    d.status = "received"
    d.flags = flags
    db.flush()  # this receipt now counts in received_milli()
    if received_milli(db, wo) >= wo.qty_milli and wo.status == "in_delivery":
        transition(db, clock, "work_order", wo, "delivered", actor=f"user:{user_id}")
    vendor = db.get(Vendor, wo.vendor_id)
    assert vendor is not None
    note = f"received {format_qty(accepted, unit)} (delivery {d.public_code})"
    if "short_delivery" in flags:
        note += f"; {flags['short_delivery']}"
    if "over_delivery" in flags:
        note += f"; {flags['over_delivery']}"
    get_channel().send(db, clock, Outbound(vendor=vendor, template="delivery_update", params={"po_code": wo.public_code, "update": note},
                                           org_id=wo.builder_org_id, rfq_id=wo.rfq_id))  # fmt: skip
    audit(db, clock, actor=f"user:{user_id}", action="delivery.received", entity="delivery", entity_id=d.id,
          org_id=wo.builder_org_id, after={"accepted_milli": accepted, "reported_milli": qty_milli, "flags": flags})  # fmt: skip
    return d


@dataclass(frozen=True)
class InvoiceIn:
    invoice_no: str
    po_code_stated: str
    amount_paise: int
    unit_price_paise: int


def expected_amount(wo: WorkOrder, qty_milli: int) -> int:
    """What the invoice should come to for qty (pro-rata on the PO's own amounts)."""
    return div_round_half_up(wo.total_paise * qty_milli, wo.qty_milli)


def check_invoice(
    db: Session, clock: Clock, wo: WorkOrder, data: InvoiceIn, file_ref: str | None, client_ref: str
) -> Invoice:
    """Compare to the PO. Any mismatch is flagged; nothing is accepted automatically."""
    existing = db.scalars(select(Invoice).where(Invoice.client_ref == client_ref)).one_or_none()
    if existing is not None:
        return existing
    got = received_milli(db, wo)
    billable = got or wo.qty_milli
    flags: dict[str, str] = {}
    if data.po_code_stated.strip().upper() != wo.public_code:
        flags["different_po"] = (
            f"Invoice refers to {data.po_code_stated or 'no PO'}, not {wo.public_code}"
        )
    if abs(data.unit_price_paise - wo.unit_price_paise) > 0:
        flags["unit_price"] = (
            f"Invoiced {format_inr(data.unit_price_paise)} per unit; PO says {format_inr(wo.unit_price_paise)}"
        )
    expected = expected_amount(wo, billable)
    if abs(data.amount_paise - expected) > TOLERANCE_PAISE:
        flags["amount"] = (
            f"Invoiced {format_inr(data.amount_paise)}; expected {format_inr(expected)} for {format_qty(billable, _unit(db, wo))} received"
        )
    inv = Invoice(builder_org_id=wo.builder_org_id, work_order_id=wo.id, file_ref=file_ref, invoice_no=data.invoice_no[:60],
                  po_code_stated=data.po_code_stated[:40], amount_paise=data.amount_paise, unit_price_paise=data.unit_price_paise,
                  mismatch_flags=flags, status="flagged" if flags else "matched", client_ref=client_ref)  # fmt: skip
    db.add(inv)
    db.flush()
    audit(db, clock, actor="system:invoice_check", action=f"invoice.{inv.status}", entity="invoice", entity_id=inv.id,
          org_id=wo.builder_org_id, after={"flags": flags, "amount": data.amount_paise, "expected": expected})  # fmt: skip
    return inv


def close(
    db: Session, clock: Clock, wo: WorkOrder, user_id: uuid.UUID, shortfall_note: str | None
) -> None:
    """Close the PO: needs an invoice that matched (or that the builder accepted despite
    flags). A short PO needs a shortfall note. Then ratings and price history update."""
    if wo.status == "closed":
        return
    got = received_milli(db, wo)
    if wo.status == "in_delivery" and got < wo.qty_milli:
        if not shortfall_note:
            raise DeliveryError(
                f"Only {format_qty(got, _unit(db, wo))} of {format_qty(wo.qty_milli, _unit(db, wo))} arrived. Add a shortfall note to close anyway."
            )
        if in_transit_milli(db, wo):
            raise DeliveryError("A delivery is still on its way; receive it first")
        wo.shortfall_note = shortfall_note[:300]
        transition(
            db, clock, "work_order", wo, "delivered", actor=f"user:{user_id}", reason="closed short"
        )
    if wo.status != "delivered":
        raise DeliveryError(f"A {wo.status.replace('_', ' ')} work order cannot be closed")
    ok_invoice = db.scalars(
        select(Invoice).where(
            Invoice.work_order_id == wo.id, Invoice.status.in_(["matched", "accepted"])
        )
    ).first()
    if ok_invoice is None:
        raise DeliveryError("Record a matching invoice (or accept a flagged one) before closing")
    transition(db, clock, "work_order", wo, "closed", actor=f"user:{user_id}")
    wo.closed_at = clock.now()
    _price_history(db, clock, wo)
    recompute_vendor(db, wo.vendor_id)
    _close_upstream(db, clock, wo, f"user:{user_id}")


def _price_history(db: Session, clock: Clock, wo: WorkOrder) -> None:
    rfq = db.get(Rfq, wo.rfq_id)
    assert rfq is not None
    _, _, item, site, _ = _ctx(db, rfq)
    if db.scalars(select(PriceHistory).where(PriceHistory.work_order_id == wo.id)).first():
        return
    landed = div_round_half_up(wo.total_paise * MILLI, wo.qty_milli)
    db.add(PriceHistory(catalog_item_id=item.id, region=site.pincode[:3], unit_price_paise=wo.unit_price_paise,
                        landed_paise=landed, price_date=to_ist(clock.now()).date(), work_order_id=wo.id))  # fmt: skip


def recompute_vendor(db: Session, vendor_id: uuid.UUID) -> None:
    """Reliability from the vendor's closed orders: on time, exact quantity, invoice
    matched first time, and how often they answer RFQs."""
    vendor = db.get(Vendor, vendor_id)
    assert vendor is not None
    closed = db.scalars(
        select(WorkOrder).where(WorkOrder.vendor_id == vendor_id, WorkOrder.status == "closed")
    ).all()
    if closed:
        n = len(closed)
        late = exact = matched = 0
        for wo in closed:
            deliveries = db.scalars(select(Delivery).where(Delivery.work_order_id == wo.id)).all()
            late += any(d.flags.get("late_days") for d in deliveries)
            exact += sum(d.received_qty_milli or 0 for d in deliveries) == wo.qty_milli and not any(
                "short_delivery" in d.flags or "over_delivery" in d.flags for d in deliveries
            )
            first = db.scalars(
                select(Invoice).where(Invoice.work_order_id == wo.id).order_by(Invoice.created_at)
            ).first()
            matched += first is not None and first.status == "matched"
        vendor.on_time_bp = (n - late) * BP // n
        vendor.qty_accuracy_bp = exact * BP // n
        vendor.invoice_match_bp = matched * BP // n
        vendor.orders_completed = n
    invited = (
        db.scalar(
            select(func.count())
            .select_from(RfqInvitation)
            .where(RfqInvitation.vendor_id == vendor_id, RfqInvitation.invited_at.is_not(None))
        )
        or 0
    )
    quoted = (
        db.scalar(
            select(func.count(func.distinct(Quote.rfq_id))).where(Quote.vendor_id == vendor_id)
        )
        or 0
    )
    if invited:
        vendor.response_bp = min(quoted, invited) * BP // invited


def _close_upstream(db: Session, clock: Clock, wo: WorkOrder, actor: str) -> None:
    rfq = db.get(Rfq, wo.rfq_id)
    assert rfq is not None
    open_pos = [
        w
        for w in db.scalars(select(WorkOrder).where(WorkOrder.rfq_id == rfq.id))
        if w.status not in ("closed", "cancelled", "vendor_declined", "expired")
    ]
    if not open_pos and can_transition("rfq", rfq.status, "closed"):
        transition(db, clock, "rfq", rfq, "closed", actor=actor)
    line = db.get(BomLine, rfq.bom_line_id)
    assert line is not None
    bom = db.get(Bom, line.bom_id)
    assert bom is not None
    statuses = list(
        db.scalars(
            select(Rfq.status)
            .join(BomLine, Rfq.bom_line_id == BomLine.id)
            .where(BomLine.bom_id == bom.id)
        )
    )
    if all(s in ("closed", "cancelled") for s in statuses) and can_transition(
        "bom", bom.status, "closed"
    ):
        transition(db, clock, "bom", bom, "closed", actor=actor)
