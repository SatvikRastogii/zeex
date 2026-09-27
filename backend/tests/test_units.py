from fractions import Fraction

import pytest

from app.domain.units import (
    Conversion,
    UnitError,
    conversion_factor,
    normalize_unit,
    price_per_canonical_paise,
    to_canonical_milli,
)

CEMENT = [Conversion("bag", "kg", Fraction(50)), Conversion("tonne", "kg", Fraction(1000))]
SAND = [Conversion("brass", "cft", Fraction(100))]


def test_cement_bag_tonne_both_ways() -> None:
    assert conversion_factor("tonne", "bag", CEMENT) == 20
    assert conversion_factor("bag", "tonne", CEMENT) == Fraction(1, 20)


def test_brass_cft_both_ways() -> None:
    assert conversion_factor("brass", "cft", SAND) == 100
    assert conversion_factor("cft", "brass", SAND) == Fraction(1, 100)


def test_to_canonical_milli() -> None:
    assert to_canonical_milli(Fraction(30), "bag", "bag", CEMENT) == 30_000
    assert to_canonical_milli(Fraction(3, 2), "tonne", "bag", CEMENT) == 30_000
    assert to_canonical_milli(Fraction(2), "brass", "cft", SAND) == 200_000


def test_inexact_conversion_rejected() -> None:
    with pytest.raises(UnitError):
        to_canonical_milli(Fraction(1, 3), "bag", "bag", CEMENT)


def test_unconvertible_unit() -> None:
    with pytest.raises(UnitError):
        conversion_factor("truck", "cft", SAND)


def test_per_tonne_price_restated_per_bag() -> None:
    # ₹7,600/tonne == ₹380/bag at 50 kg per bag
    assert price_per_canonical_paise(760000, "tonne", "bag", CEMENT) == 38000


@pytest.mark.parametrize(
    ("raw", "unit"), [("Bags", "bag"), (" MT ", "tonne"), ("cu  ft", "cft"), ("Nos.", "nos")]
)
def test_normalize_unit(raw: str, unit: str) -> None:
    assert normalize_unit(raw) == unit


def test_normalize_unknown_unit() -> None:
    with pytest.raises(UnitError):
        normalize_unit("truck")
