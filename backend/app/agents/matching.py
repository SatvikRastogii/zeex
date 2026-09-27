"""Profile Matching Agent (PROMPT.md 10.1). Plain code, no LLM.

Hard filters decide who may quote; the score (0-100) only orders the survivors.
Every exclusion and every score keeps its reason so the builder can see why.
"""

import math
import uuid
from dataclasses import dataclass, field

from app.domain.units import format_qty

# Score weights (sum 100).
W_DISTANCE, W_ON_TIME, W_PRICE, W_CREDIT, W_HEADROOM = 30, 25, 20, 10, 15
MAX_CREDIT_DAYS = 30
MIN_MATCHES = 2
MAX_INVITES = 15

SUGGESTIONS = [
    "Widen the search radius",
    "Allow other brands",
    "Allow partial supply so smaller vendors can quote",
]


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance (haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class LineCtx:
    item_code: str
    canonical_unit: str
    qty_milli: int
    site_pincode: str
    site_lat: float
    site_lng: float


@dataclass(frozen=True)
class VendorCtx:
    id: uuid.UUID
    name: str
    item_codes: tuple[str, ...]
    service_pincodes: tuple[str, ...]
    radius_km: int
    lat: float
    lng: float
    linked: bool
    link_status: str | None  # active | blocked | None (not linked)
    opted_in: bool
    opted_out: bool
    gstin_ok: bool
    capacity_milli: int  # weekly capacity for this item
    reserved_milli: int  # already reserved in the needed-by week
    on_time_bp: int
    credit_days: int
    # avg closed price vs the item median, in bp (-300 = 3% cheaper); None = no history
    price_vs_median_bp: int | None = None


@dataclass
class Scored:
    vendor: VendorCtx
    score: int
    distance_km: float
    parts: dict[str, int]
    reason: str


@dataclass
class MatchResult:
    matched: list[Scored]
    eligible: list[Scored]  # everyone who passed the filters, best first
    excluded: list[dict[str, str]] = field(default_factory=list)
    warning: str | None = None
    suggestions: list[str] = field(default_factory=list)


def exclusion(v: VendorCtx, line: LineCtx, extra_radius_km: int) -> str | None:
    """First hard filter the vendor fails, or None."""
    if line.item_code not in v.item_codes:
        return "Does not supply this item/grade"
    if not v.linked:
        return "Not linked to your organisation"
    if v.link_status == "blocked":
        return "Blocked by your organisation"
    if v.opted_out or not v.opted_in:
        return "Opted out of messages"
    if not v.gstin_ok:
        return "No valid GSTIN on record"
    km = distance_km(line.site_lat, line.site_lng, v.lat, v.lng)
    if line.site_pincode not in v.service_pincodes and km > v.radius_km + extra_radius_km:
        return f"Does not serve this site ({km:.0f} km, serves {v.radius_km + extra_radius_km} km)"
    if v.capacity_milli - v.reserved_milli <= 0:
        return "No capacity left in the needed-by week"
    return None


def score(v: VendorCtx, line: LineCtx, extra_radius_km: int) -> Scored:
    km = distance_km(line.site_lat, line.site_lng, v.lat, v.lng)
    reach = max(v.radius_km + extra_radius_km, 1)
    headroom = v.capacity_milli - v.reserved_milli
    parts = {
        "distance": round(W_DISTANCE * max(0.0, 1 - km / reach)),
        "on_time": W_ON_TIME * v.on_time_bp // 10_000,
        # -10% or better -> full marks, +10% or worse -> zero, no history -> half
        "price": (
            W_PRICE // 2
            if v.price_vs_median_bp is None
            else round(W_PRICE * min(1.0, max(0.0, (1000 - v.price_vs_median_bp) / 2000)))
        ),
        "credit": W_CREDIT * min(v.credit_days, MAX_CREDIT_DAYS) // MAX_CREDIT_DAYS,
        # headroom of 2x the order or more -> full marks
        "headroom": W_HEADROOM * min(headroom, 2 * line.qty_milli) // max(2 * line.qty_milli, 1),
    }
    if v.price_vs_median_bp is None:
        price_txt = "no price history"
    else:
        pct = abs(v.price_vs_median_bp) / 100
        side = "below" if v.price_vs_median_bp < 0 else "above"
        price_txt = "at area median" if v.price_vs_median_bp == 0 else f"{pct:.1f}% {side} median"
    reason = " · ".join(
        [
            f"{km:.0f} km",
            f"on-time {v.on_time_bp / 100:.0f}%",
            price_txt,
            f"{v.credit_days}-day credit" if v.credit_days else "no credit",
            f"capacity {format_qty(headroom, line.canonical_unit)}/wk free",
        ]
    )
    return Scored(v, sum(parts.values()), km, parts, reason)


def match(
    line: LineCtx, vendors: list[VendorCtx], *, top_n: int = 5, extra_radius_km: int = 0
) -> MatchResult:
    eligible: list[Scored] = []
    excluded: list[dict[str, str]] = []
    for v in vendors:
        why = exclusion(v, line, extra_radius_km)
        if why is None:
            eligible.append(score(v, line, extra_radius_km))
        elif line.item_code in v.item_codes and v.linked:
            # Report only linked vendors who sell the item: a builder never learns
            # about vendors outside its own network.
            excluded.append({"vendor_id": str(v.id), "vendor": v.name, "reason": why})
    # Ties: higher score, then closer, then name (stable and explainable).
    eligible.sort(key=lambda s: (-s.score, s.distance_km, s.vendor.name))
    matched = eligible[: min(top_n, MAX_INVITES)]
    result = MatchResult(matched=matched, eligible=eligible, excluded=excluded)
    if len(matched) == 0:
        result.warning = "No vendors match this line."
        result.suggestions = SUGGESTIONS
    elif len(matched) < MIN_MATCHES:
        result.warning = "Only one vendor matches; there is no competition on price."
        result.suggestions = SUGGESTIONS
    return result
