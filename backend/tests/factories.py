"""Minimal row builders for tests."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import BuilderOrg, Site


def org_with_site(db: Session, name: str = "Test Org") -> tuple[BuilderOrg, Site]:
    org = BuilderOrg(name=name, settings={})
    db.add(org)
    db.flush()
    site = Site(
        builder_org_id=org.id,
        name=f"{name} Site",
        address="Plot 1",
        area="Sector 62, Noida",
        pincode="201309",
        lat=28.62,
        lng=77.36,
    )
    db.add(site)
    db.commit()
    return org, site


def work_order_for(db: Session, vendor_id: uuid.UUID) -> Any:
    """A minimal issued PO (with its BOM/line/RFQ chain) for tests that need one."""
    from datetime import date

    from sqlalchemy import select

    from app.db.models import Bom, BomLine, Rfq, WorkOrder

    site = db.scalars(select(Site)).first()
    assert site is not None
    tag = uuid.uuid4().hex[:8]
    bom = Bom(builder_org_id=site.builder_org_id, public_code=f"BOM-F-{tag}", site_id=site.id)
    db.add(bom)
    db.flush()
    line = BomLine(builder_org_id=site.builder_org_id, bom_id=bom.id, line_no=1, raw_text="x",
                   qty_canonical_milli=1000, unit="bag", qty_entered="1", needed_by=date(2026, 10, 5))  # fmt: skip
    db.add(line)
    db.flush()
    rfq = Rfq(builder_org_id=site.builder_org_id, bom_line_id=line.id, public_code=f"RFQ-F-{tag}")
    db.add(rfq)
    db.flush()
    wo = WorkOrder(builder_org_id=site.builder_org_id, public_code=f"PO-F-{tag}", rfq_id=rfq.id,
                   vendor_id=vendor_id, qty_milli=1000, unit_price_paise=1, subtotal_paise=1,
                   gst_paise=0, total_paise=1)  # fmt: skip
    db.add(wo)
    db.flush()
    return wo
