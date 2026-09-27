"""Vendor directory and audit log for builders (UI screens 10 and 12)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import BizClock, Builder, Db, org_id, require
from app.db.audit import audit
from app.db.models import AuditLog, BuilderVendorLink, User, Vendor
from app.jobs.clock import format_ist

router = APIRouter(tags=["directory"])

Editor = Annotated[User, Depends(require("shortlist.edit"))]


def _vendor_out(v: Vendor, link: BuilderVendorLink) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "name": v.display_name,
        "legal_name": v.legal_name,
        "phone": v.phone,
        "gstin": v.gstin,
        "categories": v.categories,
        "items": v.item_codes,
        "brands": v.brands,
        "languages": v.languages,
        "credit_days": v.credit_days,
        "on_time_bp": v.on_time_bp,
        "qty_accuracy_bp": v.qty_accuracy_bp,
        "invoice_match_bp": v.invoice_match_bp,
        "response_bp": v.response_bp,
        "quality_bp": v.quality_bp,
        "orders_completed": v.orders_completed,
        "opted_out": v.opted_out_at is not None,
        "link_status": link.status,
        "notes": link.notes,
    }


@router.get("/vendors")
def vendors(user: Builder, db: Db) -> list[dict[str, Any]]:
    """Only vendors linked to this builder."""
    rows = db.execute(
        select(Vendor, BuilderVendorLink)
        .join(BuilderVendorLink, BuilderVendorLink.vendor_id == Vendor.id)
        .where(BuilderVendorLink.builder_org_id == org_id(user))
        .order_by(Vendor.display_name)
    ).all()
    return [_vendor_out(v, link) for v, link in rows]


class LinkIn(BaseModel):
    status: Literal["active", "blocked"]
    notes: str | None = Field(default=None, max_length=300)


@router.patch("/vendors/{vendor_id}")
def set_link(
    vendor_id: uuid.UUID, body: LinkIn, user: Editor, db: Db, clock: BizClock
) -> dict[str, Any]:
    link = db.scalars(
        select(BuilderVendorLink).where(
            BuilderVendorLink.builder_org_id == org_id(user),
            BuilderVendorLink.vendor_id == vendor_id,
        )
    ).one_or_none()
    vendor = db.get(Vendor, vendor_id)
    if link is None or vendor is None:
        raise HTTPException(404, "Not found")
    before = {"status": link.status, "notes": link.notes}
    link.status, link.notes = body.status, body.notes
    audit(db, clock, actor=f"user:{user.id}", action=f"vendor.{body.status}", entity="vendor", entity_id=vendor.id,
          org_id=org_id(user), before=before, after=body.model_dump())  # fmt: skip
    db.commit()
    return _vendor_out(vendor, link)


@router.get("/audit")
def audit_log(
    user: Builder, db: Db, entity: str | None = None, action: str | None = None, before: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:  # fmt: skip
    """Read-only, this organisation's entries only, newest first."""
    q = select(AuditLog).where(AuditLog.builder_org_id == org_id(user))
    if entity:
        q = q.where(AuditLog.entity == entity)
    if action:
        q = q.where(AuditLog.action.ilike(f"%{action}%"))
    if before:
        q = q.where(AuditLog.at < before)
    rows = db.scalars(
        q.order_by(AuditLog.at.desc(), AuditLog.created_at.desc()).limit(max(1, min(limit, 500)))
    )
    names = {
        str(u.id): u.name
        for u in db.scalars(select(User).where(User.builder_org_id == org_id(user)))
    }
    out = []
    for a in rows:
        kind, _, ident = a.actor.partition(":")
        out.append({
            "at": a.at, "at_display": format_ist(a.at), "actor": names.get(ident, a.actor) if kind == "user" else a.actor,
            "action": a.action, "entity": a.entity, "entity_id": a.entity_id, "before": a.before, "after": a.after,
        })  # fmt: skip
    return out
