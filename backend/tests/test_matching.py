"""Pure matcher tests (no database)."""

import uuid
from dataclasses import replace

from app.agents.matching import LineCtx, VendorCtx, distance_km, exclusion, match

LINE = LineCtx("OPC53", "bag", 30_000, "201309", 28.627, 77.373)


def vendor(name: str = "V", **kw: object) -> VendorCtx:
    base = VendorCtx(
        id=uuid.uuid4(), name=name, item_codes=("OPC53",), service_pincodes=("201309",), radius_km=40,
        lat=28.60, lng=77.35, linked=True, link_status="active", opted_in=True, opted_out=False,
        gstin_ok=True, capacity_milli=1_000_000, reserved_milli=0, on_time_bp=9000, credit_days=15,
    )  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def test_distance_noida_to_gurugram_about_38km() -> None:
    assert 36 < distance_km(28.627, 77.373, 28.46, 77.03) < 40


def test_filters_in_order() -> None:
    assert exclusion(vendor(item_codes=("PPC",)), LINE, 0) == "Does not supply this item/grade"
    assert (
        exclusion(vendor(linked=False, link_status=None), LINE, 0)
        == "Not linked to your organisation"
    )
    assert exclusion(vendor(link_status="blocked"), LINE, 0) == "Blocked by your organisation"
    assert exclusion(vendor(opted_out=True), LINE, 0) == "Opted out of messages"
    assert exclusion(vendor(opted_in=False), LINE, 0) == "Opted out of messages"
    assert exclusion(vendor(gstin_ok=False), LINE, 0) == "No valid GSTIN on record"
    assert (
        exclusion(vendor(reserved_milli=1_000_000), LINE, 0)
        == "No capacity left in the needed-by week"
    )
    assert exclusion(vendor(), LINE, 0) is None


def test_serves_by_pincode_even_when_far() -> None:
    far = vendor(lat=28.0, lng=76.0, radius_km=5)
    assert exclusion(far, LINE, 0) is None
    assert "Does not serve" in (exclusion(replace(far, service_pincodes=()), LINE, 0) or "")


def test_extra_radius_brings_vendor_in() -> None:
    v = vendor(service_pincodes=(), lat=28.46, lng=77.03, radius_km=35)  # ~38 km
    assert exclusion(v, LINE, 0) is not None
    assert exclusion(v, LINE, 5) is None


def test_zero_matches_warns_with_suggestions() -> None:
    r = match(LINE, [vendor(link_status="blocked"), vendor(opted_out=True)])
    assert r.matched == [] and r.warning and "Widen the search radius" in r.suggestions
    assert {e["reason"] for e in r.excluded} == {
        "Blocked by your organisation",
        "Opted out of messages",
    }


def test_one_match_warns() -> None:
    r = match(LINE, [vendor(), vendor(gstin_ok=False)])
    assert len(r.matched) == 1 and "no competition" in (r.warning or "")


def test_unlinked_vendor_is_invisible() -> None:
    r = match(LINE, [vendor(), vendor(name="Other network", linked=False, link_status=None)])
    assert all(e["vendor"] != "Other network" for e in r.excluded)
    assert all(s.vendor.name != "Other network" for s in r.matched)


def test_top_n() -> None:
    r = match(LINE, [vendor(name=f"V{i}") for i in range(8)], top_n=5)
    assert len(r.matched) == 5 and len(r.eligible) == 8


def test_better_vendor_scores_higher() -> None:
    good = vendor(
        name="Good", lat=28.62, lng=77.37, on_time_bp=9800, credit_days=30, price_vs_median_bp=-500
    )
    poor = vendor(
        name="Poor", lat=28.45, lng=77.60, on_time_bp=6000, credit_days=0, price_vs_median_bp=800
    )
    r = match(LINE, [poor, good])
    assert [s.vendor.name for s in r.matched] == ["Good", "Poor"]
    assert r.matched[0].score > r.matched[1].score
    assert "5.0% below median" in r.matched[0].reason


def test_ties_broken_by_distance_then_name() -> None:
    a = vendor(name="Beta")
    b = vendor(name="Alpha")
    near = vendor(name="Zeta", lat=28.627, lng=77.373)
    # identical inputs except position -> same score parts except distance
    r = match(LINE, [a, b])
    assert [s.vendor.name for s in r.matched] == ["Alpha", "Beta"]
    r2 = match(LINE, [a, near])
    assert r2.matched[0].vendor.name == "Zeta"


def test_score_is_bounded() -> None:
    best = vendor(
        lat=28.627, lng=77.373, on_time_bp=10_000, credit_days=60, price_vs_median_bp=-5000
    )
    assert match(LINE, [best]).matched[0].score == 100
    worst = vendor(service_pincodes=("201309",), lat=29.5, lng=78.5, on_time_bp=0, credit_days=0,
                   price_vs_median_bp=5000, capacity_milli=1)  # fmt: skip
    assert 0 <= match(LINE, [worst]).matched[0].score <= 5
