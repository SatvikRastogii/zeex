"""Reference prices (PROMPT.md 10.5): median closed price for the item in the region
over the last 90 days, else the median of the RFQ's current confirmed quotes."""

import statistics
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PriceHistory, Quote

LOOKBACK_DAYS = 90


def reference_median_paise(
    db: Session, item_id: uuid.UUID, region: str, today: date, rfq_id: uuid.UUID | None = None
) -> tuple[int | None, str]:
    """(median unit price per canonical unit before GST, where it came from)."""
    closed = db.scalars(
        select(PriceHistory.unit_price_paise).where(
            PriceHistory.catalog_item_id == item_id,
            PriceHistory.region == region,
            PriceHistory.price_date >= today - timedelta(days=LOOKBACK_DAYS),
        )
    ).all()
    if closed:
        return int(
            statistics.median(closed)
        ), f"median of {len(closed)} closed orders, last 90 days"
    if rfq_id is not None:
        current = [
            p
            for p in db.scalars(
                select(Quote.price_per_canonical_paise).where(
                    Quote.rfq_id == rfq_id, Quote.status == "confirmed"
                )
            )
            if p is not None
        ]
        if current:
            return int(
                statistics.median(current)
            ), f"median of {len(current)} confirmed quotes on this RFQ"
    return None, "no reference price yet"
