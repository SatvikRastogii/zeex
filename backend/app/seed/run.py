"""Deterministic, idempotent demo seed.

Static rows are upserted by natural key. History rows are keyed by BOM title, and the
random draws are generated up front so a re-run makes the same choices and skips rows
that already exist. Vendor ratings are then recomputed from the history.

Run: uv run python -m app.seed.run
"""

import random
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.ids import delivery_code, next_code, rfq_code
from app.db.models import (
    Base,
    Bom,
    BomLine,
    BuilderOrg,
    BuilderVendorLink,
    CatalogItem,
    Delivery,
    DemoClockState,
    PriceHistory,
    Rfq,
    Site,
    UnitConversion,
    User,
    Vendor,
    WorkOrder,
)
from app.db.session import get_engine
from app.domain.gstin import is_valid_gstin
from app.domain.money import apply_bp, div_round_half_up
from app.domain.settings import DEFAULT_ORG_SETTINGS
from app.jobs.clock import Clock, SystemClock, ist_datetime, ist_today
from app.seed import data

SEED = 20260925
MILLI = 1000


def upsert[M: Base](db: Session, model: type[M], key: dict[str, Any], values: dict[str, Any]) -> M:
    row = db.scalars(select(model).filter_by(**key)).one_or_none()
    if row is None:
        row = model(**key, **values)
        db.add(row)
    else:
        for k, v in values.items():
            setattr(row, k, v)
    db.flush()
    return row


def seed_static(db: Session, clock: Clock) -> None:
    upsert(
        db,
        User,
        {"phone": data.ADMIN["phone"]},
        {
            "name": data.ADMIN["name"],
            "role": "admin",
            "builder_org_id": None,
            "approval_limit_paise": None,
            "is_active": True,
        },
    )

    for o in data.ORGS:
        org = upsert(
            db,
            BuilderOrg,
            {"name": o["name"]},
            {"gstin": o["gstin"], "settings": DEFAULT_ORG_SETTINGS},
        )
        for phone, name, role in o["users"]:
            limit = data.PM_LIMIT_PAISE if role == "purchase_manager" else None
            upsert(
                db,
                User,
                {"phone": phone},
                {
                    "name": name,
                    "role": role,
                    "builder_org_id": org.id,
                    "approval_limit_paise": limit,
                    "is_active": True,
                },
            )
        for name, address, area, pin, lat, lng, contact, cphone in o["sites"]:
            upsert(
                db,
                Site,
                {"builder_org_id": org.id, "name": name},
                {
                    "address": address,
                    "area": area,
                    "pincode": pin,
                    "lat": lat,
                    "lng": lng,
                    "contact_name": contact,
                    "contact_phone": cphone,
                },
            )

    items: dict[str, CatalogItem] = {}
    for code, cat, name, grade, unit, aliases, hsn, gst, _ in data.CATALOG:
        items[code] = upsert(
            db,
            CatalogItem,
            {"code": code},
            {
                "category": cat,
                "name": name,
                "grade": grade,
                "canonical_unit": unit,
                "aliases": aliases,
                "hsn": hsn,
                "default_gst_bp": gst,
            },
        )
    for item_code, frm, to, num, den in data.CONVERSIONS:
        item_id = items[item_code].id if item_code else None
        upsert(
            db,
            UnitConversion,
            {"item_id": item_id, "from_unit": frm, "to_unit": to},
            {"numerator": num, "denominator": den},
        )

    orgs = {o.name: o for o in db.scalars(select(BuilderOrg))}
    opt_in = ist_datetime(date(2026, 1, 1), time(10, 0))
    for v in data.VENDORS:
        assert v["gstin"] is None or is_valid_gstin(v["gstin"]), v["gstin"]
        codes = list(v["items"])
        vendor = upsert(
            db,
            Vendor,
            {"phone": v["phone"]},
            {
                "legal_name": f"{v['name']} Pvt Ltd",
                "display_name": v["name"],
                "gstin": v["gstin"],
                "opted_in_at": opt_in,
                "opted_out_at": opt_in + timedelta(days=200) if v.get("opted_out") else None,
                "languages": v["langs"],
                "categories": sorted({items[c].category for c in codes}),
                "item_codes": codes,
                "brands": v["brands"],
                "service_pincodes": v["pins"],
                "service_radius_km": v["radius"],
                "lat": v["at"][0],
                "lng": v["at"][1],
                "capacity_per_week": {c: q * MILLI for c, q in v["items"].items()},
                "min_order_qty": {c: max(1, q // 20) * MILLI for c, q in v["items"].items()},
                "credit_days": v["credit"],
                "persona": v["persona"],
            },
        )
        for org_name in v.get("orgs", data.ALL_ORGS):
            status = "blocked" if org_name in v.get("blocked_by", []) else "active"
            upsert(
                db,
                BuilderVendorLink,
                {"builder_org_id": orgs[org_name].id, "vendor_id": vendor.id},
                {"status": status},
            )

    if db.get(DemoClockState, 1) is None:
        db.add(DemoClockState(id=1, offset_seconds=0))
    db.flush()


@dataclass(frozen=True)
class HistorySpec:
    title: str
    org_name: str
    site_idx: int
    item_code: str
    vendor_phone: str
    qty: int  # canonical units
    unit_price_paise: int
    freight_paise: int
    days_ago: int
    late_days: int  # 0 = on time
    short_milli: int  # quantity short on receipt


def history_specs() -> list[HistorySpec]:
    """Pure and deterministic: same list every run."""
    rng = random.Random(SEED)
    base = {c[0]: c[8] for c in data.CATALOG}
    specs = []
    for i in range(data.HISTORY_COUNT):
        org = data.ORGS[i % len(data.ORGS)]
        item = data.HISTORY_ITEMS[i % len(data.HISTORY_ITEMS)]
        candidates = [
            v
            for v in data.VENDORS
            if item in v["items"]
            and v["gstin"]
            and not v.get("opted_out")
            and org["name"] in v.get("orgs", data.ALL_ORGS)
            and org["name"] not in v.get("blocked_by", [])
        ]
        vendor = candidates[rng.randrange(len(candidates))]
        lo, hi = data.HISTORY_QTY[item]
        on_time = rng.random() < vendor["reliability"]
        specs.append(
            HistorySpec(
                title=f"Seed history {i + 1:02d}",
                org_name=org["name"],
                site_idx=i % 2,
                item_code=item,
                vendor_phone=vendor["phone"],
                qty=rng.randint(lo, hi),
                # ±6% around the base price, whole rupees
                unit_price_paise=base[item] * rng.randint(94, 106) // 100 // 100 * 100,
                freight_paise=rng.choice([0, 0, 150000, 250000]),
                days_ago=10 + (i * 170) // data.HISTORY_COUNT,
                late_days=0 if on_time else rng.randint(1, 4),
                short_milli=0 if rng.random() < 0.85 else MILLI * rng.randint(1, 5),
            )
        )
    return specs


def seed_history(db: Session, clock: Clock) -> None:
    today = ist_today(clock)
    orgs = {o.name: o for o in db.scalars(select(BuilderOrg))}
    items = {i.code: i for i in db.scalars(select(CatalogItem))}
    vendors = {v.phone: v for v in db.scalars(select(Vendor))}
    existing = set(db.scalars(select(Bom.title).where(Bom.title.like("Seed history %"))))

    for s in history_specs():
        if s.title in existing:
            continue
        org, item, vendor = orgs[s.org_name], items[s.item_code], vendors[s.vendor_phone]
        site = db.scalars(
            select(Site).where(Site.builder_org_id == org.id).order_by(Site.name)
        ).all()[s.site_idx]
        ordered = today - timedelta(days=s.days_ago)
        needed_by = ordered + timedelta(days=5)
        received = needed_by + timedelta(days=s.late_days)
        qty_milli = s.qty * MILLI

        bom = Bom(
            builder_org_id=org.id,
            public_code=next_code(db, "bom", clock),
            site_id=site.id,
            status="closed",
            source="manual",
            title=s.title,
        )
        db.add(bom)
        db.flush()
        line = BomLine(
            builder_org_id=org.id,
            bom_id=bom.id,
            line_no=1,
            catalog_item_id=item.id,
            raw_text=item.name,
            qty_canonical_milli=qty_milli,
            unit=item.canonical_unit,
            qty_entered=str(s.qty),
            needed_by=needed_by,
            status="closed",
        )
        db.add(line)
        db.flush()
        rfq = Rfq(
            builder_org_id=org.id,
            bom_line_id=line.id,
            public_code=rfq_code(bom.public_code, 1),
            status="closed",
            bid_window_opens_at=ist_datetime(ordered - timedelta(days=2), time(10)),
            bid_window_closes_at=ist_datetime(ordered - timedelta(days=1), time(10)),
        )
        db.add(rfq)
        db.flush()
        subtotal = div_round_half_up(s.unit_price_paise * qty_milli, MILLI)
        gst = apply_bp(subtotal, item.default_gst_bp)
        po = WorkOrder(
            builder_org_id=org.id,
            public_code=next_code(db, "work_order", clock),
            rfq_id=rfq.id,
            vendor_id=vendor.id,
            qty_milli=qty_milli,
            unit_price_paise=s.unit_price_paise,
            subtotal_paise=subtotal,
            gst_paise=gst,
            freight_paise=s.freight_paise,
            total_paise=subtotal + gst + s.freight_paise,
            delivery_date=needed_by,
            status="closed",
            confirmed_at=ist_datetime(ordered, time(12)),
            closed_at=ist_datetime(received, time(17)),
            shortfall_note="Short delivery accepted" if s.short_milli else None,
        )
        db.add(po)
        db.flush()
        db.add(
            Delivery(
                builder_org_id=org.id,
                public_code=delivery_code(po.public_code, 1),
                work_order_id=po.id,
                seq=1,
                dispatched_at=ist_datetime(received, time(8)),
                vehicle_no=f"UP16AT{1000 + s.qty % 9000}",
                dispatched_qty_milli=qty_milli - s.short_milli,
                received_qty_milli=qty_milli - s.short_milli,
                received_at=ist_datetime(received, time(15)),
                status="received",
                flags={"late_days": s.late_days} if s.late_days else {},
            )
        )
        landed = s.unit_price_paise + div_round_half_up(s.freight_paise * MILLI, qty_milli)
        db.add(
            PriceHistory(
                catalog_item_id=item.id,
                region=site.pincode[:3],
                unit_price_paise=s.unit_price_paise,
                landed_paise=apply_bp(landed, 10000 + item.default_gst_bp),
                price_date=ordered,
                work_order_id=po.id,
            )
        )
        db.flush()


def recompute_ratings(db: Session) -> None:
    """Vendor reliability from closed work orders (on-time and quantity accuracy)."""
    for vendor in db.scalars(select(Vendor)):
        rows = db.execute(
            select(WorkOrder.qty_milli, Delivery.received_qty_milli, Delivery.flags)
            .join(Delivery, Delivery.work_order_id == WorkOrder.id)
            .where(WorkOrder.vendor_id == vendor.id, WorkOrder.status == "closed")
        ).all()
        vendor.orders_completed = len(rows)
        if not rows:
            continue
        on_time = sum(1 for _, _, flags in rows if not flags.get("late_days"))
        exact = sum(1 for qty, got, _ in rows if got == qty)
        vendor.on_time_bp = div_round_half_up(on_time * 10000, len(rows))
        vendor.qty_accuracy_bp = div_round_half_up(exact * 10000, len(rows))
    db.flush()


def run(db: Session, clock: Clock) -> None:
    seed_static(db, clock)
    seed_history(db, clock)
    recompute_ratings(db)
    db.commit()


def main() -> None:
    with Session(get_engine()) as db:
        run(db, SystemClock())
    print("seed: done")


if __name__ == "__main__":
    main()
