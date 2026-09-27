"""Work Order Agent (PROMPT.md 10.6): approval -> POs, capacity, vendor confirmation,
runner-up. Plain code."""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.evaluation import current_recommendation
from app.agents.outreach import _ctx
from app.channels.base import Outbound
from app.channels.inbound import ROUTERS
from app.channels.simulated import get_channel
from app.db.audit import audit
from app.db.ids import next_code
from app.db.models import (
    Bom,
    BomLine,
    CapacityReservation,
    Message,
    Quote,
    Rfq,
    RfqInvitation,
    User,
    Vendor,
    WorkOrder,
)
from app.domain.money import div_round_half_up, format_inr
from app.domain.states import can_transition, transition
from app.domain.units import format_qty
from app.domain.working_hours import add_working_hours, next_working_time
from app.files import get_file_store
from app.jobs.clock import Clock, format_ist, ist_today, ist_week_start
from app.jobs.queue import enqueue, handler

MILLI = 1000


class AwardError(Exception):
    """Shown to the builder as is."""


@dataclass(frozen=True)
class Allocation:
    vendor_id: uuid.UUID
    qty_milli: int


def _q(n: Fraction) -> int:
    return div_round_half_up(n.numerator, n.denominator)


def po_amounts(q: Quote, qty_milli: int, supply_milli: int, default_gst_bp: int) -> dict[str, int]:
    """Line amounts in paise. Freight/unloading are quoted for the vendor's whole supply
    and pro-rated when the PO is for part of it."""
    unit = q.price_per_canonical_paise or 0
    gst_bp = q.gst_bp if q.gst_bp is not None else default_gst_bp
    gross = Fraction(unit * qty_milli, MILLI)
    if q.gst_included:
        subtotal = _q(gross * 10_000 / (10_000 + gst_bp))
        gst = _q(gross) - subtotal
    else:
        subtotal = _q(gross)
        gst = div_round_half_up(subtotal * gst_bp, 10_000)
    extra = (0 if q.freight_included else q.freight_paise) + q.unloading_paise
    freight = div_round_half_up(extra * qty_milli, max(supply_milli, 1))
    return {"unit_price_paise": unit, "subtotal_paise": subtotal, "gst_paise": gst, "freight_paise": freight,
            "total_paise": subtotal + gst + freight}  # fmt: skip


def latest_quote(db: Session, rfq_id: uuid.UUID, vendor_id: uuid.UUID) -> Quote | None:
    return db.scalars(
        select(Quote)
        .where(Quote.rfq_id == rfq_id, Quote.vendor_id == vendor_id, Quote.status == "confirmed")
        .order_by(Quote.revision.desc())
    ).first()


def plan(db: Session, rfq: Rfq, choice: str, allow_above_max: bool) -> list[Allocation]:
    """choice: 'l1' | 'split' | a vendor id (runner-up / any qualified vendor)."""
    rec = current_recommendation(db, rfq.id)
    if rec is None:
        raise AwardError("There is no recommendation to approve yet")
    _, line, _, _, _ = _ctx(db, rfq)
    if choice == "split":
        if not rec.split_proposal or rec.split_proposal["shortfall_milli"]:
            raise AwardError("There is no complete split proposal to approve")
        return [
            Allocation(uuid.UUID(a["vendor_id"]), a["qty_milli"])
            for a in rec.split_proposal["allocations"]
        ]
    vendor_id = str(rec.l1_vendor_id) if choice == "l1" else choice
    row = next((r for r in rec.ranked if r["vendor_id"] == vendor_id), None)
    if row is None or not row["qualified"]:
        raise AwardError("That vendor has no qualified offer")
    if row["above_max"] and not allow_above_max:
        raise AwardError(
            "The offer is above your maximum price; tick the override to approve it anyway"
        )
    if not row["can_cover_alone"]:
        raise AwardError("This vendor cannot supply the full quantity; approve the split instead")
    return [Allocation(uuid.UUID(vendor_id), line.qty_canonical_milli)]


def check_validity(db: Session, clock: Clock, rfq: Rfq, allocs: list[Allocation]) -> None:
    today = ist_today(clock)
    for a in allocs:
        q = latest_quote(db, rfq.id, a.vendor_id)
        if q is None or q.validity_until is None or q.validity_until < today:
            v = db.get(Vendor, a.vendor_id)
            name = v.display_name if v else "The vendor"
            when = f" on {q.validity_until:%d %b}" if q and q.validity_until else ""
            raise AwardError(
                f"{name}'s offer expired{when}. Ask them to reconfirm before approving."
            )


def reserve(
    db: Session,
    vendor: Vendor,
    item_id: uuid.UUID,
    item_code: str,
    week: date,
    qty_milli: int,
    wo: WorkOrder,
) -> None:
    """Capacity check under a row lock on the vendor, so two builders' awards for the
    same vendor and week are serialised: the second sees the first's reservation."""
    db.execute(select(Vendor.id).where(Vendor.id == vendor.id).with_for_update())
    db.execute(
        select(CapacityReservation.id)
        .where(CapacityReservation.vendor_id == vendor.id, CapacityReservation.catalog_item_id == item_id,
               CapacityReservation.week_start == week, CapacityReservation.released_at.is_(None))
        .with_for_update()
    )  # fmt: skip
    reserved = db.scalar(
        select(func.coalesce(func.sum(CapacityReservation.qty_reserved_milli), 0)).where(
            CapacityReservation.vendor_id == vendor.id, CapacityReservation.catalog_item_id == item_id,
            CapacityReservation.week_start == week, CapacityReservation.released_at.is_(None),
        )
    ) or 0  # fmt: skip
    capacity = int(vendor.capacity_per_week.get(item_code, 0))
    if reserved + qty_milli > capacity:
        raise AwardError(
            f"{vendor.display_name} no longer has capacity for the week of {week:%d %b}: "
            f"{format_qty(capacity - reserved, '')} left, {format_qty(qty_milli, '')} needed "
            "(another order was confirmed first). Offer the runner-up instead."
        )
    db.add(
        CapacityReservation(
            vendor_id=vendor.id,
            catalog_item_id=item_id,
            week_start=week,
            qty_reserved_milli=qty_milli,
            work_order_id=wo.id,
        )
    )


def release(db: Session, clock: Clock, wo: WorkOrder) -> None:
    res = db.scalars(
        select(CapacityReservation).where(CapacityReservation.work_order_id == wo.id)
    ).one_or_none()
    if res is not None and res.released_at is None:
        res.released_at = clock.now()


def issue(
    db: Session, clock: Clock, rfq: Rfq, allocs: list[Allocation], approver: User
) -> list[WorkOrder]:
    org, line, item, site, cfg = _ctx(db, rfq)
    week = ist_week_start(line.needed_by)
    hours = cfg["working_hours"]
    rec = current_recommendation(db, rfq.id)
    supply = {r["vendor_id"]: r["supply_milli"] for r in (rec.ranked if rec else [])}
    orders = []
    for a in allocs:
        q = latest_quote(db, rfq.id, a.vendor_id)
        vendor = db.get(Vendor, a.vendor_id)
        assert q is not None and vendor is not None
        amounts = po_amounts(
            q,
            a.qty_milli,
            supply.get(str(a.vendor_id), a.qty_milli) or a.qty_milli,
            item.default_gst_bp,
        )
        wo = WorkOrder(
            builder_org_id=rfq.builder_org_id, public_code=next_code(db, "work_order", clock), rfq_id=rfq.id,
            vendor_id=vendor.id, quote_id=q.id, qty_milli=a.qty_milli, delivery_date=q.delivery_date,
            partial_allowed=line.partial_allowed or len(allocs) > 1, status="issued",
            confirm_by=add_working_hours(clock.now(), int(cfg["po_confirm_working_hours"]), hours["start"], hours["end"]),
            **amounts,
        )  # fmt: skip
        db.add(wo)
        db.flush()
        reserve(db, vendor, item.id, item.code, week, a.qty_milli, wo)
        wo.pdf_ref = get_file_store().save(f"pos/{rfq.builder_org_id}", f"{wo.public_code}.pdf",
                                           po_pdf(wo, org.name, org.gstin, vendor, item.name, item.canonical_unit, site, line.needed_by))  # fmt: skip
        orders.append(wo)
    db.flush()
    for wo in orders:
        _notify_winner(db, clock, rfq, wo)
        enqueue(
            db,
            "po_confirm_timeout",
            wo.confirm_by or clock.now(),
            {"work_order_id": str(wo.id)},
            dedupe_key=f"po-timeout:{wo.id}",
        )
        audit(db, clock, actor=f"user:{approver.id}", action="work_order.issued", entity="work_order", entity_id=wo.id,
              org_id=wo.builder_org_id, after={"code": wo.public_code, "total": wo.total_paise})  # fmt: skip
    _notify_losers(db, clock, rfq, {a.vendor_id for a in allocs})
    return orders


def _notify_winner(db: Session, clock: Clock, rfq: Rfq, wo: WorkOrder) -> None:
    _, line, item, site, _ = _ctx(db, rfq)
    vendor = db.get(Vendor, wo.vendor_id)
    assert vendor is not None
    get_channel().send(db, clock, Outbound(vendor=vendor, template="award_notice", params={"rfq_code": rfq.public_code},
                                           org_id=rfq.builder_org_id, rfq_id=rfq.id))  # fmt: skip
    get_channel().send(db, clock, Outbound(
        vendor=vendor, template="po_issued",
        params={  # the exact address and site contact go to the winner only, after award
            "po_code": wo.public_code, "item": item.name, "qty": format_qty(wo.qty_milli, item.canonical_unit),
            "price": f"{format_inr(wo.unit_price_paise)} per {item.canonical_unit}",
            "address": f"{site.address} ({site.pincode})", "needed_by": line.needed_by.strftime("%d %b %Y"),
            "contact": f"{site.contact_name or 'Site office'} {site.contact_phone or ''}".strip(),
            "confirm_by": format_ist(wo.confirm_by) if wo.confirm_by else "",
        },
        payload={"work_order_id": str(wo.id)}, org_id=rfq.builder_org_id, rfq_id=rfq.id,
    ))  # fmt: skip


def _notify_losers(db: Session, clock: Clock, rfq: Rfq, winners: set[uuid.UUID]) -> None:
    quoted = set(
        db.scalars(
            select(Quote.vendor_id).where(
                Quote.rfq_id == rfq.id, Quote.status.in_(["confirmed", "superseded"])
            )
        )
    )
    already = set(
        db.scalars(
            select(Message.vendor_id).where(
                Message.rfq_id == rfq.id, Message.template_name == "not_selected"
            )
        )
    )
    for vid in quoted - winners - already:
        vendor = db.get(Vendor, vid)
        assert vendor is not None
        get_channel().send(db, clock, Outbound(vendor=vendor, template="not_selected", params={"rfq_code": rfq.public_code},
                                               org_id=rfq.builder_org_id, rfq_id=rfq.id))  # fmt: skip


def po_pdf(
    wo: WorkOrder,
    buyer: str,
    buyer_gstin: str | None,
    vendor: Vendor,
    item: str,
    unit: str,
    site: Any,
    needed_by: date,
) -> bytes:  # noqa: ANN401
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=True)
    y = 800
    rows = [
        ("Helvetica-Bold", 16, f"WORK ORDER {wo.public_code}"),
        ("Helvetica", 10, f"Buyer: {buyer}  GSTIN {buyer_gstin or '-'}"),
        ("Helvetica", 10, f"Supplier: {vendor.legal_name}  GSTIN {vendor.gstin or '-'}"),
        ("Helvetica", 10, f"Deliver to: {site.address} ({site.pincode})"),
        ("Helvetica", 10, f"Site contact: {site.contact_name or '-'} {site.contact_phone or ''}"),
        (
            "Helvetica",
            10,
            f"Needed by: {needed_by:%d %b %Y}   Promised: {wo.delivery_date:%d %b %Y}"
            if wo.delivery_date
            else f"Needed by: {needed_by:%d %b %Y}",
        ),
        ("Helvetica", 10, ""),
        (
            "Helvetica-Bold",
            11,
            f"{item}: {format_qty(wo.qty_milli, unit)} at {format_inr(wo.unit_price_paise)} per {unit}",
        ),
        (
            "Helvetica",
            10,
            f"Subtotal {format_inr(wo.subtotal_paise)}   GST {format_inr(wo.gst_paise)}   Freight/unloading {format_inr(wo.freight_paise)}",
        ),
        ("Helvetica-Bold", 11, f"Total {format_inr(wo.total_paise)}"),
        ("Helvetica", 10, ""),
        ("Helvetica", 9, "Demo document generated by Z-Procure. Fictional parties."),
    ]
    for font, size, text in rows:
        c.setFont(font, size)
        c.drawString(50, y, text.replace("₹", "Rs "))
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


# --- vendor confirmation, decline, expiry, runner-up ---------------------------------


def _po_for_reply(db: Session, msg: Message) -> WorkOrder | None:
    reply_to = (msg.payload or {}).get("reply_to")
    if reply_to:
        original = db.get(Message, uuid.UUID(reply_to))
        wo_id = (original.payload or {}).get("work_order_id") if original else None
        if wo_id:
            return db.get(WorkOrder, uuid.UUID(wo_id))
    return db.scalars(
        select(WorkOrder).where(WorkOrder.vendor_id == msg.vendor_id, WorkOrder.rfq_id == msg.rfq_id, WorkOrder.status == "issued")
        .order_by(WorkOrder.created_at.desc())
    ).first()  # fmt: skip


def route(db: Session, clock: Clock, msg: Message) -> bool:
    button = (msg.payload or {}).get("button")
    if msg.direction != "in" or button not in ("Confirm", "Decline"):
        return False
    wo = _po_for_reply(db, msg)
    if wo is None or wo.vendor_id != msg.vendor_id:
        return False
    vendor = db.get(Vendor, wo.vendor_id)
    assert vendor is not None
    if wo.status != "issued":
        get_channel().send(db, clock, Outbound(vendor=vendor, text=f"Work order {wo.public_code} is already {wo.status.replace('_', ' ')}.",
                                               org_id=wo.builder_org_id, rfq_id=wo.rfq_id))  # fmt: skip
        return True
    if button == "Confirm":
        transition(db, clock, "work_order", wo, "vendor_confirmed", actor=f"vendor:{vendor.id}")
        wo.confirmed_at = clock.now()
        get_channel().send(db, clock, Outbound(vendor=vendor, text=f"Thank you. Work order {wo.public_code} is confirmed.",
                                               org_id=wo.builder_org_id, rfq_id=wo.rfq_id))  # fmt: skip
    else:
        transition(db, clock, "work_order", wo, "vendor_declined", actor=f"vendor:{vendor.id}")
        release(db, clock, wo)
        offer_runner_up(db, clock, wo, "vendor declined the work order")
    return True


ROUTERS.insert(0, route)


@handler("po_confirm_timeout")
def po_confirm_timeout(db: Session, clock: Clock, payload: dict[str, Any]) -> None:
    wo = db.get(WorkOrder, uuid.UUID(str(payload["work_order_id"])))
    if wo is None or wo.status != "issued" or (wo.confirm_by and clock.now() < wo.confirm_by):
        return
    transition(
        db,
        clock,
        "work_order",
        wo,
        "expired",
        actor="system:work_orders",
        reason="not confirmed in time",
    )
    release(db, clock, wo)
    offer_runner_up(db, clock, wo, "vendor did not confirm in time")


def offer_runner_up(db: Session, clock: Clock, failed: WorkOrder, reason: str) -> None:
    """Put the next valid offer in front of the builder (one tap). If none is still
    valid, the builder can re-open bidding."""
    rfq = db.get(Rfq, failed.rfq_id)
    assert rfq is not None
    for q in db.scalars(
        select(Quote).where(
            Quote.rfq_id == rfq.id, Quote.vendor_id == failed.vendor_id, Quote.status == "confirmed"
        )
    ):
        transition(db, clock, "quote", q, "withdrawn", actor="system:work_orders", reason=reason)
    rec = current_recommendation(db, rfq.id)
    tried = set(db.scalars(select(WorkOrder.vendor_id).where(WorkOrder.rfq_id == rfq.id)))
    today = ist_today(clock)
    runner = None
    for r in rec.ranked if rec else []:
        if (
            not r["qualified"]
            or r["above_max"]
            or not r["can_cover_alone"]
            or uuid.UUID(r["vendor_id"]) in tried
        ):
            continue
        offer = latest_quote(db, rfq.id, uuid.UUID(r["vendor_id"]))
        if offer and offer.validity_until and offer.validity_until >= today:
            runner = r
            break
    live = [
        w
        for w in db.scalars(select(WorkOrder).where(WorkOrder.rfq_id == rfq.id))
        if w.status in ("issued", "vendor_confirmed", "in_delivery", "delivered", "closed")
    ]
    if not live and can_transition("rfq", rfq.status, "awaiting_approval"):
        transition(
            db, clock, "rfq", rfq, "awaiting_approval", actor="system:work_orders", reason=reason
        )
    rfq.match_report = {**rfq.match_report, "runner_up": runner["vendor_id"] if runner else None,
                        "runner_up_reason": reason, "failed_po": failed.public_code}  # fmt: skip
    audit(db, clock, actor="system:work_orders", action="work_order.runner_up", entity="rfq", entity_id=rfq.id,
          org_id=rfq.builder_org_id, after={"failed": failed.public_code, "reason": reason,
                                            "runner_up": runner["vendor"] if runner else None})  # fmt: skip


def cancel_po(db: Session, clock: Clock, wo: WorkOrder, actor: str, reason: str) -> None:
    transition(db, clock, "work_order", wo, "cancelled", actor=actor, reason=reason)
    release(db, clock, wo)
    vendor = db.get(Vendor, wo.vendor_id)
    assert vendor is not None
    get_channel().send(db, clock, Outbound(vendor=vendor, template="delivery_update",
                                           params={"po_code": wo.public_code, "update": f"cancelled by the buyer ({reason}). Please do not dispatch."},
                                           org_id=wo.builder_org_id, rfq_id=wo.rfq_id))  # fmt: skip


def advance_bom(db: Session, clock: Clock, bom_id: uuid.UUID, actor: str) -> None:
    """Walk the BOM forward to partially_awarded / awarded as its RFQs are awarded."""
    bom = db.get(Bom, bom_id)
    assert bom is not None
    statuses = [
        s
        for s in db.scalars(
            select(Rfq.status)
            .join(BomLine, Rfq.bom_line_id == BomLine.id)
            .where(BomLine.bom_id == bom_id)
        )
        if s != "cancelled"
    ]
    done = sum(s in ("awarded", "closed") for s in statuses)
    if not done:
        return
    target = "awarded" if done == len(statuses) else "partially_awarded"
    for step in ("in_progress", "awaiting_approval", target):
        if bom.status != target and can_transition("bom", bom.status, step):
            transition(db, clock, "bom", bom, step, actor=actor)


def reinvite(db: Session, clock: Clock, rfq: Rfq, actor: str) -> None:
    """No valid runner-up: re-open bidding with the same vendors (except those who failed)."""
    _, _, _, _, cfg = _ctx(db, rfq)
    hours = cfg["working_hours"]
    failed = set(db.scalars(select(WorkOrder.vendor_id).where(WorkOrder.rfq_id == rfq.id)))
    transition(
        db,
        clock,
        "rfq",
        rfq,
        "bidding",
        actor=actor,
        reason="re-opened after the award fell through",
    )
    opens = next_working_time(clock.now(), hours["start"], hours["end"])
    rfq.bid_window_opens_at, rfq.bid_window_closes_at = (
        opens,
        opens + timedelta(hours=int(cfg["bid_window_hours"])),
    )
    rfq.window_extended = False
    n = rfq.revision = rfq.revision + 1
    for inv in db.scalars(select(RfqInvitation).where(RfqInvitation.rfq_id == rfq.id)):
        if inv.vendor_id in failed or inv.status in ("skipped_opted_out", "removed", "proposed"):
            continue
        inv.status, inv.reminded_at = "queued", None
        enqueue(
            db,
            "send_invite",
            opens,
            {"invitation_id": str(inv.id)},
            ordering_key=f"vendor:{inv.vendor_id}",
            dedupe_key=f"invite:{inv.id}:r{n}",
        )
    enqueue(
        db,
        "bid_close",
        rfq.bid_window_closes_at,
        {"rfq_id": str(rfq.id)},
        dedupe_key=f"close:{rfq.id}:rebid{n}",
    )
