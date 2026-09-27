"""RFQ views and shortlist editing (matching review)."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.match_runner import MATCHABLE, load_context, run_matching
from app.agents.matching import MAX_INVITES, exclusion, score
from app.agents.outreach import OutreachError, send_rfqs
from app.api.deps import BizClock, Builder, Db, org_id, require
from app.db.audit import audit
from app.db.models import Bom, BomLine, CatalogItem, Rfq, RfqInvitation, Site, User, Vendor
from app.db.tenancy import get_owned
from app.domain.states import transition
from app.domain.units import format_qty

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
