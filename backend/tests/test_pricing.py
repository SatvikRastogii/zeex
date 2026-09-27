from app.domain.pricing import Counter, VendorTerms, floor_paise, next_counter, reached_target

EX_GST = VendorTerms(
    gst_bp=1800,
    gst_included=False,
    freight_paise=0,
    unloading_paise=0,
    qty_milli=30_000,
    compare_incl_gst=True,
)


def landed(rupees: int) -> int:
    return EX_GST.landed(rupees * 100)


def test_landed_and_back() -> None:
    assert landed(380) == 44840
    assert EX_GST.unit_for_landed(44840) == 38000
    assert EX_GST.unit_for_landed(44841) == 38100  # rounded up to a whole rupee


def test_freight_is_taken_out_when_restating() -> None:
    t = VendorTerms(
        gst_bp=0,
        gst_included=True,
        freight_paise=75000,
        unloading_paise=0,
        qty_milli=30_000,
        compare_incl_gst=True,
    )
    assert t.landed(36000) == 38500
    assert t.unit_for_landed(37500) == 35000  # ₹375 landed = ₹350 + ₹25 freight


def test_floor_is_92_percent_of_reference() -> None:
    assert floor_paise(40000, 99999) == 36800
    assert floor_paise(None, 40000) == 36800


def test_non_benchmark_vendor_asked_to_match() -> None:
    c = next_counter(
        round_no=1, vendor_landed=landed(400), benchmark_landed=landed(385), floor=0, terms=EX_GST
    )
    assert c.kind == "match_benchmark" and c.unit_paise == 38500 and c.lower_offer_exists


def test_benchmark_vendor_asked_for_3_then_2_percent() -> None:
    v = landed(385)
    c1 = next_counter(round_no=1, vendor_landed=v, benchmark_landed=v, floor=0, terms=EX_GST)
    assert c1.kind == "improve" and not c1.lower_offer_exists
    assert c1.unit_paise == 37400  # 385 * 0.97 = 373.45 -> ₹374 (rounded up)
    c2 = next_counter(round_no=2, vendor_landed=v, benchmark_landed=v, floor=0, terms=EX_GST)
    assert c2.unit_paise == 37800  # 385 * 0.98 = 377.3 -> ₹378


def test_round_three_is_best_and_final_without_a_number() -> None:
    c = next_counter(
        round_no=3, vendor_landed=landed(400), benchmark_landed=landed(380), floor=0, terms=EX_GST
    )
    assert c == Counter("best_and_final", None, None, True)


def test_never_below_floor() -> None:
    floor = landed(378)
    c = next_counter(
        round_no=1,
        vendor_landed=landed(400),
        benchmark_landed=landed(350),
        floor=floor,
        terms=EX_GST,
    )
    assert c.landed_paise is not None and c.landed_paise >= floor
    assert c.unit_paise == 37800


def test_at_floor_nothing_to_ask() -> None:
    floor = landed(380)
    c = next_counter(
        round_no=1, vendor_landed=floor, benchmark_landed=floor, floor=floor, terms=EX_GST
    )
    assert c.kind == "hold" and c.unit_paise is None


def test_lower_offer_only_when_true() -> None:
    v = landed(380)
    assert not next_counter(
        round_no=1, vendor_landed=v, benchmark_landed=v, floor=0, terms=EX_GST
    ).lower_offer_exists


def test_reached_target() -> None:
    assert (
        reached_target(44840, 45000)
        and not reached_target(44840, 44000)
        and not reached_target(44840, None)
    )
