from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    BuilderVendorLink,
    PriceHistory,
    User,
    Vendor,
    WorkOrder,
)
from app.domain.gstin import is_valid_gstin
from app.jobs.clock import FixedClock
from app.seed.run import run

CLOCK = FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC))


def snapshot(db: Session) -> dict[str, Any]:
    counts = {
        t.name: db.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables
    }
    vendors = db.execute(
        select(
            Vendor.phone, Vendor.on_time_bp, Vendor.qty_accuracy_bp, Vendor.orders_completed
        ).order_by(Vendor.phone)
    ).all()
    prices = db.execute(
        select(
            PriceHistory.region, PriceHistory.unit_price_paise, PriceHistory.price_date
        ).order_by(PriceHistory.price_date, PriceHistory.unit_price_paise)
    ).all()
    codes = db.scalars(select(WorkOrder.public_code).order_by(WorkOrder.public_code)).all()
    return {"counts": counts, "vendors": vendors, "prices": prices, "codes": codes}


def test_seed_is_idempotent(db: Session) -> None:
    run(db, CLOCK)
    first = snapshot(db)
    run(db, CLOCK)
    assert snapshot(db) == first


def test_seed_shape(db: Session) -> None:
    run(db, CLOCK)
    snap = snapshot(db)["counts"]
    assert snap["builder_orgs"] == 3
    assert snap["users"] == 10  # 3 per org + admin
    assert snap["sites"] == 6
    assert snap["catalog_items"] == 12
    assert snap["vendors"] == 24
    assert snap["work_orders"] == 30
    assert snap["price_history"] == 30
    assert (
        db.scalar(
            select(func.count()).where(
                User.role == "purchase_manager", User.approval_limit_paise == 50_000_000
            )
        )
        == 3
    )


def test_seed_edge_case_vendors(db: Session) -> None:
    run(db, CLOCK)
    vendors = db.scalars(select(Vendor)).all()
    assert sum(1 for v in vendors if v.gstin is None) == 1
    assert sum(1 for v in vendors if v.opted_out_at is not None) == 1
    assert all(is_valid_gstin(v.gstin) for v in vendors if v.gstin)
    blocked = db.scalars(
        select(BuilderVendorLink).where(BuilderVendorLink.status == "blocked")
    ).all()
    assert len(blocked) == 1
    # no single cement vendor can cover a 2,000-bag order (split-award scenario)
    cement = [v for v in vendors if "OPC53" in v.capacity_per_week]
    assert max(v.capacity_per_week["OPC53"] for v in cement) < 2_000_000
    assert min(v.capacity_per_week["OPC53"] for v in cement) <= 150_000
