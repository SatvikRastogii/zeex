"""Evaluation Agent (PROMPT.md 10.4): loads confirmed quotes, applies the rules in
domain/evaluation.py and stores a Recommendation. Runs automatically at bid close."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.match_runner import reserved_milli
from app.agents.outreach import CLOSE_HOOKS, _ctx
from app.db.audit import audit
from app.db.models import Quote, Recommendation, Rfq, RfqInvitation, Vendor
from app.domain.evaluation import LineInfo, QuoteInfo, pick_l1, score_all, split_award
from app.domain.states import transition
from app.domain.working_hours import next_working_time
from app.jobs.clock import Clock, ist_today, ist_week_start, to_ist
from app.jobs.queue import enqueue

MIN_QUOTES = 2
NEGOTIATION_DAYS = 2  # a quote must stay valid this long after bid close

# Run after a recommendation is ready (the negotiation agent registers in Stage 9).
EVALUATED_HOOKS: list[Callable[[Session, Clock, Rfq, Recommendation], None]] = []


def evaluate(
    db: Session, clock: Clock, rfq: Rfq, *, actor: str, allow_above_max: bool = False
) -> Recommendation:
    org, line, item, _, cfg = _ctx(db, rfq)
    quotes = db.scalars(
        select(Quote).where(Quote.rfq_id == rfq.id, Quote.status == "confirmed")
    ).all()
    vendors = {
        v.id: v
        for v in db.scalars(select(Vendor).where(Vendor.id.in_([q.vendor_id for q in quotes])))
    }
    week = ist_week_start(line.needed_by)
    reserved = reserved_milli(db, list(vendors), item.id, week)
    infos = []
    for q in quotes:
        v = vendors[q.vendor_id]
        cap = int(v.capacity_per_week.get(item.code, 0))
        infos.append(
            QuoteInfo(
                quote_id=q.id,
                code=q.public_code,
                vendor_id=v.id,
                vendor=v.display_name,
                price_per_canonical_paise=q.price_per_canonical_paise,
                gst_included=q.gst_included,
                gst_bp=q.gst_bp if q.gst_bp is not None else item.default_gst_bp,
                freight_paise=q.freight_paise,
                freight_included=q.freight_included,
                unloading_paise=q.unloading_paise,
                delivery_date=q.delivery_date,
                validity_until=q.validity_until,
                payment_terms_days=q.payment_terms_days or 0,
                qty_offered_milli=q.qty_offered_milli,
                received_at=q.received_at or q.created_at,
                flags=q.flags,
                on_time_bp=v.on_time_bp,
                reliability_bp=(v.on_time_bp + v.qty_accuracy_bp + v.response_bp) // 3,
                quality_bp=v.quality_bp,
                capacity_left_milli=cap - reserved.get(v.id, 0),
                min_order_milli=int(v.min_order_qty.get(item.code, 0)),
            )
        )
    close_day = (
        to_ist(rfq.bid_window_closes_at).date() if rfq.bid_window_closes_at else ist_today(clock)
    )
    line_info = LineInfo(
        line.qty_canonical_milli, line.needed_by, line.partial_allowed, item.canonical_unit
    )
    ranked = score_all(
        infos,
        line_info,
        cfg["weights"],
        gst_mode=cfg["gst_mode"],
        today=ist_today(clock),
        validity_needed=close_day + timedelta(days=NEGOTIATION_DAYS),
        max_price_paise=rfq.max_price_paise,
    )
    l1, lowest = pick_l1(ranked, allow_above_max)
    split = split_award(ranked, line_info)
    shortlist = [s.q.vendor_id for s in ranked if s.qualified and not s.above_max][
        : rfq.shortlist_size
    ]

    db.execute(
        update(Recommendation).where(Recommendation.rfq_id == rfq.id).values(is_current=False)
    )
    rec = Recommendation(
        builder_org_id=rfq.builder_org_id,
        rfq_id=rfq.id,
        ranked=[s.as_dict() | {"shortlisted": s.q.vendor_id in shortlist} for s in ranked],
        l1_vendor_id=l1.q.vendor_id if l1 else None,
        lowest_price_vendor_id=lowest.q.vendor_id if lowest else None,
        split_proposal=split,
        generated_at=clock.now(),
        is_current=True,
    )
    db.add(rec)
    db.flush()
    audit(db, clock, actor=actor, action="rfq.evaluate", entity="rfq", entity_id=rfq.id, org_id=rfq.builder_org_id,
          after={"quotes": len(infos), "qualified": sum(s.qualified for s in ranked),
                 "l1": l1.q.vendor if l1 else None, "lowest": lowest.q.vendor if lowest else None,
                 "split": bool(split), "weights": cfg["weights"], "gst_mode": cfg["gst_mode"]})  # fmt: skip
    return rec


def on_bid_close(db: Session, clock: Clock, rfq: Rfq) -> None:
    """Registered with the outreach bid-close job."""
    confirmed = db.scalars(
        select(Quote).where(Quote.rfq_id == rfq.id, Quote.status == "confirmed")
    ).all()
    if len(confirmed) < MIN_QUOTES and not rfq.window_extended:
        extend_window(db, clock, rfq)
        return
    if not confirmed:
        transition(db, clock, "rfq", rfq, "insufficient_quotes", actor="system:evaluation",
                   reason="no confirmed quotes after one extension")  # fmt: skip
        return
    rec = evaluate(db, clock, rfq, actor="system:evaluation")
    if len(confirmed) < MIN_QUOTES:
        # One quote: never negotiate while implying competition. Straight to the builder.
        rfq.match_report = {**rfq.match_report, "single_quote": True}
        transition(db, clock, "rfq", rfq, "awaiting_approval", actor="system:evaluation",
                   reason="single quote; negotiation skipped")  # fmt: skip
        return
    for hook in EVALUATED_HOOKS:
        hook(db, clock, rfq, rec)


def extend_window(db: Session, clock: Clock, rfq: Rfq) -> None:
    _, _, _, _, cfg = _ctx(db, rfq)
    hours = cfg["working_hours"]
    transition(
        db,
        clock,
        "rfq",
        rfq,
        "insufficient_quotes",
        actor="system:evaluation",
        reason="fewer than 2 confirmed quotes",
    )
    opens = next_working_time(clock.now(), hours["start"], hours["end"])
    rfq.bid_window_closes_at = opens + timedelta(hours=int(cfg["bid_window_hours"]))
    rfq.window_extended = True
    transition(
        db,
        clock,
        "rfq",
        rfq,
        "bidding",
        actor="system:evaluation",
        reason="bid window extended once",
    )
    # Everyone invited who has not quoted gets one more nudge.
    db.execute(
        update(RfqInvitation)
        .where(RfqInvitation.rfq_id == rfq.id, RfqInvitation.status == "invited")
        .values(reminded_at=None)
    )
    enqueue(
        db, "bid_reminder", opens, {"rfq_id": str(rfq.id)}, dedupe_key=f"reminder:{rfq.id}:extended"
    )
    enqueue(
        db,
        "bid_close",
        rfq.bid_window_closes_at,
        {"rfq_id": str(rfq.id)},
        dedupe_key=f"close:{rfq.id}:2",
    )
    audit(db, clock, actor="system:evaluation", action="rfq.window_extended", entity="rfq", entity_id=rfq.id,
          org_id=rfq.builder_org_id, after={"closes_at": rfq.bid_window_closes_at.isoformat(),
                                            "note": "Fewer than 2 quotes; extended once"})  # fmt: skip


CLOSE_HOOKS.append(on_bid_close)


def current_recommendation(db: Session, rfq_id: uuid.UUID) -> Recommendation | None:
    return db.scalars(
        select(Recommendation).where(Recommendation.rfq_id == rfq_id, Recommendation.is_current)
    ).first()


def ranked_lookup(rec: Recommendation) -> dict[str, dict[str, Any]]:
    return {r["vendor_id"]: r for r in rec.ranked}
