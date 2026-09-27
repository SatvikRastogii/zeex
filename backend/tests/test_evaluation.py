"""Evaluation rules (pure): landed cost, disqualification, scoring, ties, L1, splits."""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.domain.evaluation import (
    LineInfo,
    QuoteInfo,
    landed_cost_paise,
    pick_l1,
    score_all,
    split_award,
)

TODAY = date(2026, 9, 25)
LINE = LineInfo(
    qty_milli=30_000, needed_by=date(2026, 10, 5), partial_allowed=False, canonical_unit="bag"
)
W = {"price": 50, "delivery": 20, "payment": 10, "reliability": 10, "quality": 10}
T0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)


def quote(vendor: str = "V", **kw: Any) -> QuoteInfo:
    base = QuoteInfo(
        quote_id=uuid.uuid4(), code=f"QT-{vendor}", vendor_id=uuid.uuid4(), vendor=vendor,
        price_per_canonical_paise=38000, gst_included=False, gst_bp=1800, freight_paise=0,
        freight_included=True, unloading_paise=0, delivery_date=date(2026, 10, 1),
        validity_until=date(2026, 10, 20), payment_terms_days=15, qty_offered_milli=None,
        received_at=T0, flags={}, on_time_bp=9000, reliability_bp=9000, quality_bp=8000,
        capacity_left_milli=1_000_000, min_order_milli=0,
    )  # fmt: skip
    return replace(base, **kw)


def rank(
    *qs: QuoteInfo, line: LineInfo = LINE, max_price: int | None = None, gst_mode: str = "incl"
) -> list[Any]:
    return score_all(
        list(qs),
        line,
        W,
        gst_mode=gst_mode,
        today=TODAY,
        validity_needed=date(2026, 9, 28),
        max_price_paise=max_price,
    )


# --- landed cost -----------------------------------------------------------------------


def test_freight_quote_vs_delivered_quote() -> None:
    """₹360 + ₹25/bag freight (₹750 for 30 bags) lands above ₹375 delivered."""
    with_freight = quote(
        price_per_canonical_paise=36000, freight_included=False, freight_paise=75000
    )
    delivered = quote(price_per_canonical_paise=37500)
    assert landed_cost_paise(with_freight, 30_000, "excl") == 38500
    assert landed_cost_paise(delivered, 30_000, "excl") == 37500
    ranked = rank(with_freight, delivered, gst_mode="excl")
    assert ranked[0].landed_paise == 37500


def test_gst_added_only_when_comparing_incl_and_quote_excludes() -> None:
    ex = quote(price_per_canonical_paise=38000, gst_included=False)
    inc = quote(price_per_canonical_paise=44840, gst_included=True)
    assert landed_cost_paise(ex, 30_000, "incl") == 44840
    assert landed_cost_paise(inc, 30_000, "incl") == 44840
    assert landed_cost_paise(inc, 30_000, "excl") == 38000
    assert landed_cost_paise(ex, 30_000, "excl") == 38000


def test_rounding_happens_once_at_the_end() -> None:
    # 1 paisa of freight over 3 bags would round to 0 per bag if rounded early
    q = quote(price_per_canonical_paise=100, freight_included=False, freight_paise=2, gst_bp=0)
    assert landed_cost_paise(q, 3_000, "excl") == 101  # 100 + 0.67 -> 101


def test_unloading_spread_over_quantity() -> None:
    q = quote(unloading_paise=30000)
    assert landed_cost_paise(q, 30_000, "excl") == 38000 + 1000


# --- disqualification -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"delivery_date": date(2026, 10, 9)}, "misses the needed-by date"),
        ({"delivery_date": None}, "No delivery date"),
        ({"validity_until": date(2026, 9, 20)}, "expired"),
        ({"validity_until": date(2026, 9, 27)}, "too short"),
        ({"qty_offered_milli": 20_000}, "partial supply is not allowed"),
        ({"price_per_canonical_paise": None}, "cannot be compared"),
        ({"flags": {"item_not_in_rate_list": True}}, "Wrong item"),
    ],
)
def test_disqualification_reasons(change: dict[str, Any], reason: str) -> None:
    (s,) = rank(quote(**change))
    assert not s.qualified and any(reason in r for r in s.reasons)


def test_partial_offer_ok_when_partial_allowed() -> None:
    (s,) = rank(quote(qty_offered_milli=20_000), line=replace(LINE, partial_allowed=True))
    assert s.qualified and not s.can_cover_alone


def test_above_max_is_flagged_not_hidden() -> None:
    cheap, dear = (
        quote("Cheap", price_per_canonical_paise=38000),
        quote("Dear", price_per_canonical_paise=40000),
    )
    ranked = rank(cheap, dear, max_price=46000)
    assert [s.q.vendor for s in ranked] == ["Cheap", "Dear"]
    assert ranked[1].above_max and ranked[1].qualified
    l1, _ = pick_l1([ranked[1]])
    assert l1 is None  # not recommended without an override
    l1, _ = pick_l1([ranked[1]], allow_above_max=True)
    assert l1 is not None


# --- scoring and ties ---------------------------------------------------------------------


def test_weights_must_sum_to_100() -> None:
    with pytest.raises(AssertionError):
        score_all(
            [quote()],
            LINE,
            {**W, "price": 60},
            gst_mode="incl",
            today=TODAY,
            validity_needed=TODAY,
            max_price_paise=None,
        )


def test_l1_by_score_can_differ_from_lowest_price() -> None:
    cheap_slow = quote(
        "CheapSlow",
        price_per_canonical_paise=37000,
        delivery_date=date(2026, 10, 5),
        payment_terms_days=0,
        reliability_bp=5000,
        quality_bp=5000,
    )
    fair_fast = quote(
        "FairFast",
        price_per_canonical_paise=38000,
        delivery_date=date(2026, 9, 27),
        payment_terms_days=30,
        reliability_bp=9800,
        quality_bp=9500,
    )
    ranked = rank(cheap_slow, fair_fast)
    l1, lowest = pick_l1(ranked)
    assert l1 is not None and lowest is not None
    assert l1.q.vendor == "FairFast" and lowest.q.vendor == "CheapSlow"


def test_price_weight_dominates_when_everything_else_equal() -> None:
    ranked = rank(
        quote("A", price_per_canonical_paise=39000), quote("B", price_per_canonical_paise=38000)
    )
    assert ranked[0].q.vendor == "B" and ranked[0].score_bp > ranked[1].score_bp


def test_tie_breaks_landed_then_delivery_then_rating_then_time() -> None:
    a = quote("A")
    b = quote("B", received_at=T0 + timedelta(minutes=5))
    assert [s.q.vendor for s in rank(b, a)] == ["A", "B"]  # same everything: earlier quote wins
    c = quote("C", on_time_bp=9900)
    assert rank(a, c)[0].q.vendor == "C"  # higher rating breaks the tie first


def test_score_parts_are_reported() -> None:
    (s,) = rank(quote())
    assert set(s.parts) == set(W) and s.score_bp == sum(s.parts.values())


# --- split awards ---------------------------------------------------------------------


BIG = replace(LINE, qty_milli=2_000_000, partial_allowed=True)


def test_no_split_when_one_vendor_can_cover() -> None:
    ranked = rank(
        quote("Big", capacity_left_milli=5_000_000),
        quote("Small", capacity_left_milli=150_000),
        line=BIG,
    )
    assert split_award(ranked, BIG) is None


def test_split_across_capacity_limited_vendors() -> None:
    ranked = rank(
        quote("A", price_per_canonical_paise=38000, capacity_left_milli=1_000_000),
        quote("B", price_per_canonical_paise=38500, capacity_left_milli=800_000),
        quote("C", price_per_canonical_paise=39000, capacity_left_milli=1_500_000),
        line=BIG,
    )
    split = split_award(ranked, BIG)
    assert split is not None
    assert [(a["vendor"], a["qty_milli"]) for a in split["allocations"]] == [
        ("A", 1_000_000),
        ("B", 800_000),
        ("C", 200_000),
    ]
    assert split["covered_milli"] == 2_000_000 and split["shortfall_milli"] == 0


def test_split_skips_vendor_below_minimum_order() -> None:
    ranked = rank(
        quote("A", capacity_left_milli=1_950_000),
        quote(
            "B",
            price_per_canonical_paise=38500,
            capacity_left_milli=500_000,
            min_order_milli=100_000,
        ),
        quote("C", price_per_canonical_paise=39000, capacity_left_milli=500_000),
        line=BIG,
    )
    split = split_award(ranked, BIG)
    assert split is not None
    assert [a["vendor"] for a in split["allocations"]] == [
        "A",
        "C",
    ]  # B's minimum is 100, only 50 left
    assert split["skipped"][0]["vendor"] == "B"


def test_shortfall_reported_with_options() -> None:
    ranked = rank(
        quote("A", capacity_left_milli=900_000), quote("B", capacity_left_milli=600_000), line=BIG
    )
    split = split_award(ranked, BIG)
    assert split is not None
    assert split["covered_milli"] == 1_500_000 and split["shortfall_milli"] == 500_000
    assert "Extend the bid window to get more quotes" in split["options"]


def test_split_notes_when_partial_not_allowed() -> None:
    line = replace(BIG, partial_allowed=False)
    ranked = rank(
        quote("A", capacity_left_milli=1_000_000),
        quote("B", capacity_left_milli=1_000_000),
        line=line,
    )
    split = split_award(ranked, line)
    assert split is not None and "does not allow partial" in split["note"]
