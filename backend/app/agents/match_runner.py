"""Loads matching inputs from the database, runs the matcher, stores the shortlist."""

import statistics
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agents.matching import LineCtx, MatchResult, VendorCtx, match
from app.db.audit import audit
from app.db.models import (
    Bom,
    BomLine,
    BuilderOrg,
    BuilderVendorLink,
    CapacityReservation,
    CatalogItem,
    PriceHistory,
    Rfq,
    RfqInvitation,
    Site,
    Vendor,
    WorkOrder,
)
from app.domain.gstin import is_valid_gstin
from app.domain.settings import org_settings
from app.domain.states import transition
from app.jobs.clock import Clock, ist_week_start

MATCHABLE = {"draft", "matching", "no_vendors_matched"}


def reserved_milli(
    db: Session, vendor_ids: list[uuid.UUID], item_id: uuid.UUID, week: date
) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(CapacityReservation.vendor_id, func.sum(CapacityReservation.qty_reserved_milli))
        .where(
            CapacityReservation.vendor_id.in_(vendor_ids),
            CapacityReservation.catalog_item_id == item_id,
            CapacityReservation.week_start == week,
            CapacityReservation.released_at.is_(None),
        )
        .group_by(CapacityReservation.vendor_id)
    )
    return {vid: int(total) for vid, total in rows}


def price_position_bp(db: Session, item_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Each vendor's average closed price vs the item's median, in basis points."""
    rows = db.execute(
        select(WorkOrder.vendor_id, PriceHistory.unit_price_paise)
        .join(WorkOrder, WorkOrder.id == PriceHistory.work_order_id)
        .where(PriceHistory.catalog_item_id == item_id)
    ).all()
    if not rows:
        return {}
    median = statistics.median(p for _, p in rows)
    by_vendor: dict[uuid.UUID, list[int]] = defaultdict(list)
    for vid, p in rows:
        by_vendor[vid].append(p)
    return {
        vid: round((statistics.mean(ps) - median) * 10_000 / median)
        for vid, ps in by_vendor.items()
    }


def load_context(db: Session, rfq: Rfq) -> tuple[LineCtx, list[VendorCtx], BomLine, CatalogItem]:
    line = db.get(BomLine, rfq.bom_line_id)
    assert line is not None and line.catalog_item_id is not None
    item = db.get(CatalogItem, line.catalog_item_id)
    bom = db.get(Bom, line.bom_id)
    assert bom is not None and item is not None
    site = db.get(Site, bom.site_id)
    assert site is not None
    ctx = LineCtx(
        item.code, item.canonical_unit, line.qty_canonical_milli, site.pincode, site.lat, site.lng
    )

    vendors = db.scalars(select(Vendor).where(Vendor.item_codes.contains([item.code]))).all()
    links = {
        link.vendor_id: link.status
        for link in db.scalars(
            select(BuilderVendorLink).where(BuilderVendorLink.builder_org_id == rfq.builder_org_id)
        )
    }
    week = ist_week_start(line.needed_by)
    reserved = reserved_milli(db, [v.id for v in vendors], item.id, week)
    prices = price_position_bp(db, item.id)
    ctxs = [
        VendorCtx(
            id=v.id,
            name=v.display_name,
            item_codes=tuple(v.item_codes),
            service_pincodes=tuple(v.service_pincodes),
            radius_km=v.service_radius_km,
            lat=v.lat,
            lng=v.lng,
            linked=v.id in links,
            link_status=links.get(v.id),
            opted_in=v.opted_in_at is not None,
            opted_out=v.opted_out_at is not None,
            gstin_ok=is_valid_gstin(v.gstin),
            capacity_milli=int(v.capacity_per_week.get(item.code, 0)),
            reserved_milli=reserved.get(v.id, 0),
            on_time_bp=v.on_time_bp,
            credit_days=v.credit_days,
            price_vs_median_bp=prices.get(v.id),
        )
        for v in vendors
    ]
    return ctx, ctxs, line, item


def run_matching(
    db: Session, clock: Clock, rfq: Rfq, *, actor: str, extra_radius_km: int = 0
) -> MatchResult:
    """Replace the proposed shortlist with a fresh match. Invitations already sent stay."""
    org = db.get(BuilderOrg, rfq.builder_org_id)
    assert org is not None
    top_n = org_settings(org.settings)["match_top_n"]
    line_ctx, vendors, _, _ = load_context(db, rfq)
    result = match(line_ctx, vendors, top_n=top_n, extra_radius_km=extra_radius_km)

    db.execute(
        delete(RfqInvitation).where(
            RfqInvitation.rfq_id == rfq.id, RfqInvitation.status.in_(["proposed", "removed"])
        )
    )
    for s in result.matched:
        db.add(
            RfqInvitation(
                builder_org_id=rfq.builder_org_id,
                rfq_id=rfq.id,
                vendor_id=s.vendor.id,
                match_score=s.score,
                match_reasons={
                    "summary": s.reason,
                    "parts": s.parts,
                    "distance_km": round(s.distance_km, 1),
                },
                status="proposed",
            )
        )
    rfq.match_report = {
        "excluded": result.excluded,
        "warning": result.warning,
        "suggestions": result.suggestions,
        "extra_radius_km": extra_radius_km,
        "eligible": len(result.eligible),
        "run_at": clock.now().isoformat(),
    }
    if rfq.status != "matching":
        transition(db, clock, "rfq", rfq, "matching", actor=actor)
    if not result.matched:
        transition(db, clock, "rfq", rfq, "no_vendors_matched", actor=actor, reason=result.warning)
    audit(
        db,
        clock,
        actor=actor,
        action="rfq.match",
        entity="rfq",
        entity_id=rfq.id,
        org_id=rfq.builder_org_id,
        after={
            "matched": [s.vendor.name for s in result.matched],
            "extra_radius_km": extra_radius_km,
        },
    )
    return result
