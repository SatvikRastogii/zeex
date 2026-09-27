"""Evaluation Agent rules (PROMPT.md 10.4). Pure functions, integer paise, no LLM.

Landed cost per canonical unit = unit price
    + GST (when comparing incl. GST and the quote excludes it; the reverse removes it)
    + freight / qty + unloading / qty,
computed exactly and rounded half up once, at the end.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from fractions import Fraction
from typing import Any

from app.domain.money import div_round_half_up

MILLI = 1000
BP = 10_000


@dataclass(frozen=True)
class LineInfo:
    qty_milli: int
    needed_by: date
    partial_allowed: bool
    canonical_unit: str


@dataclass(frozen=True)
class QuoteInfo:
    quote_id: uuid.UUID
    code: str
    vendor_id: uuid.UUID
    vendor: str
    price_per_canonical_paise: int | None
    gst_included: bool
    gst_bp: int
    freight_paise: int
    freight_included: bool
    unloading_paise: int
    delivery_date: date | None
    validity_until: date | None
    payment_terms_days: int
    qty_offered_milli: int | None
    received_at: datetime
    flags: dict[str, Any]
    # vendor facts
    on_time_bp: int
    reliability_bp: int
    quality_bp: int
    capacity_left_milli: int  # remaining weekly capacity in the needed-by week
    min_order_milli: int


@dataclass
class Scored:
    q: QuoteInfo
    landed_paise: int | None
    qualified: bool
    reasons: list[str]
    above_max: bool = False
    can_cover_alone: bool = True
    supply_milli: int = 0
    parts: dict[str, int] = field(default_factory=dict)  # component scores in bp of the weight
    score_bp: int = 0  # 0..10000 (= 0..100.00)

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote_id": str(self.q.quote_id),
            "code": self.q.code,
            "vendor_id": str(self.q.vendor_id),
            "vendor": self.q.vendor,
            "landed_paise": self.landed_paise,
            "qualified": self.qualified,
            "reasons": self.reasons,
            "above_max": self.above_max,
            "can_cover_alone": self.can_cover_alone,
            "supply_milli": self.supply_milli,
            "parts": self.parts,
            "score": round(self.score_bp / 100, 2),
            "delivery_date": self.q.delivery_date.isoformat() if self.q.delivery_date else None,
            "validity_until": self.q.validity_until.isoformat() if self.q.validity_until else None,
            "payment_terms_days": self.q.payment_terms_days,
            "flags": self.q.flags,
        }


def landed_cost_paise(q: QuoteInfo, qty_milli: int, gst_mode: str) -> int | None:
    """Landed cost per canonical unit, exact until one final half-up rounding."""
    if q.price_per_canonical_paise is None or qty_milli <= 0:
        return None
    price = Fraction(q.price_per_canonical_paise)
    gst = Fraction(BP + q.gst_bp, BP)
    if gst_mode == "incl" and not q.gst_included:
        price *= gst
    elif gst_mode == "excl" and q.gst_included:
        price /= gst
    qty_units = Fraction(qty_milli, MILLI)
    if not q.freight_included:
        price += Fraction(q.freight_paise) / qty_units
    price += Fraction(q.unloading_paise) / qty_units
    return div_round_half_up(price.numerator, price.denominator)


def disqualification(q: QuoteInfo, line: LineInfo, today: date, validity_needed: date) -> list[str]:
    reasons = []
    if q.price_per_canonical_paise is None:
        reasons.append("Price cannot be compared (unit not convertible)")
    if q.flags.get("item_not_in_rate_list"):
        reasons.append("Wrong item: RFQ item not in the vendor's rate list")
    if q.delivery_date is None:
        reasons.append("No delivery date given")
    elif q.delivery_date > line.needed_by:
        reasons.append(
            f"Delivery {q.delivery_date:%d %b} misses the needed-by date {line.needed_by:%d %b}"
        )
    if q.validity_until is None or q.validity_until < today:
        reasons.append("Quote validity has expired")
    elif q.validity_until < validity_needed:
        reasons.append(
            f"Validity until {q.validity_until:%d %b} is too short (needed until {validity_needed:%d %b})"
        )
    if (
        q.qty_offered_milli is not None
        and q.qty_offered_milli < line.qty_milli
        and not line.partial_allowed
    ):
        reasons.append("Offers less than the full quantity and partial supply is not allowed")
    return reasons


def score_all(
    quotes: list[QuoteInfo],
    line: LineInfo,
    weights: dict[str, int],
    *,
    gst_mode: str,
    today: date,
    validity_needed: date,
    max_price_paise: int | None,
) -> list[Scored]:
    """Rank quotes: qualified first by score, ties broken by landed cost, delivery,
    rating, then quote time. Disqualified quotes follow with their reasons."""
    assert sum(weights.values()) == 100, "weights must sum to 100"
    scored = []
    for q in quotes:
        landed = landed_cost_paise(
            q, min(q.qty_offered_milli or line.qty_milli, line.qty_milli), gst_mode
        )
        reasons = disqualification(q, line, today, validity_needed)
        supply = min(
            q.qty_offered_milli or line.qty_milli, line.qty_milli, max(q.capacity_left_milli, 0)
        )
        s = Scored(
            q,
            landed,
            qualified=not reasons and landed is not None,
            reasons=reasons,
            supply_milli=supply,
        )
        s.can_cover_alone = supply >= line.qty_milli
        s.above_max = bool(max_price_paise and landed and landed > max_price_paise)
        scored.append(s)

    ok = [s for s in scored if s.qualified]
    if ok:
        best_landed = min(s.landed_paise for s in ok if s.landed_paise)
        earliest = min(s.q.delivery_date for s in ok if s.q.delivery_date)
        span = max((line.needed_by - earliest).days, 1)
        max_credit = max(max(s.q.payment_terms_days for s in ok), 1)
        for s in ok:
            assert s.landed_paise is not None and s.q.delivery_date is not None
            comp = {  # each in bp (0..10000) before weighting
                "price": best_landed * BP // s.landed_paise,
                "delivery": max(0, BP - (s.q.delivery_date - earliest).days * BP // span),
                "payment": s.q.payment_terms_days * BP // max_credit,
                "reliability": s.q.reliability_bp,
                "quality": s.q.quality_bp,
            }
            s.parts = {k: comp[k] * weights[k] // 100 for k in comp}
            s.score_bp = sum(s.parts.values())
    ok.sort(
        key=lambda s: (
            -s.score_bp,
            s.landed_paise,
            s.q.delivery_date,
            -s.q.on_time_bp,
            s.q.received_at,
        )
    )
    rest = sorted((s for s in scored if not s.qualified), key=lambda s: s.q.vendor)
    return ok + rest


def pick_l1(
    ranked: list[Scored], allow_above_max: bool = False
) -> tuple[Scored | None, Scored | None]:
    """(L1 = best overall score, lowest landed price) among qualified quotes.
    An offer above the builder's max price is not recommended without an override."""
    ok = [s for s in ranked if s.qualified and (allow_above_max or not s.above_max)]
    l1 = ok[0] if ok else None
    lowest = min(ok, key=lambda s: (s.landed_paise, s.q.received_at)) if ok else None
    return l1, lowest


def split_award(ranked: list[Scored], line: LineInfo) -> dict[str, Any] | None:
    """When no qualified vendor can supply the whole quantity: allocate by score,
    within each vendor's capacity and minimum order, until covered. Never silently
    short: any remainder is reported as a shortfall with options."""
    ok = [s for s in ranked if s.qualified and not s.above_max]
    if not ok or any(s.can_cover_alone for s in ok):
        return None
    remaining = line.qty_milli
    allocations = []
    skipped = []
    for s in ok:
        if remaining <= 0:
            break
        take = min(s.supply_milli, remaining)
        if take <= 0:
            continue
        if take < s.q.min_order_milli:
            skipped.append(
                {"vendor": s.q.vendor, "reason": "remaining quantity is below their minimum order"}
            )
            continue
        allocations.append({
            "vendor_id": str(s.q.vendor_id), "vendor": s.q.vendor, "quote_id": str(s.q.quote_id),
            "qty_milli": take, "landed_paise": s.landed_paise,
            "total_paise": div_round_half_up((s.landed_paise or 0) * take, MILLI),
        })  # fmt: skip
        remaining -= take
    out: dict[str, Any] = {
        "allocations": allocations,
        "skipped": skipped,
        "covered_milli": line.qty_milli - remaining,
        "shortfall_milli": max(remaining, 0),
        "total_paise": sum(a["total_paise"] for a in allocations),
    }
    if not line.partial_allowed:
        out["note"] = "This line does not allow partial supply; approving a split changes that."
    if remaining > 0:
        out["options"] = [
            "Extend the bid window to get more quotes",
            "Widen the vendor search radius",
            "Allow later delivery for the remaining quantity",
        ]
    return out
