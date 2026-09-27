"""Negotiation pricing engine (PROMPT.md 10.5). Code decides every number.

All comparisons are in landed paise per canonical unit (what the buyer really pays).
A counter is then restated in the vendor's own terms (their GST basis, freight and
unloading), rounded UP to a whole rupee so it never lands below the floor.
"""

from dataclasses import dataclass
from fractions import Fraction

from app.domain.money import div_round_half_up

FLOOR_BP = 9200  # never counter below 92% of the reference median
BENCHMARK_ASK_BP = {1: 300, 2: 200}  # round 3 = "best and final", no number


@dataclass(frozen=True)
class VendorTerms:
    """How a vendor's quoted unit price turns into landed cost."""

    gst_bp: int
    gst_included: bool
    freight_paise: int  # for the whole supply (0 if included)
    unloading_paise: int
    qty_milli: int  # quantity the freight/unloading is spread over
    compare_incl_gst: bool

    def landed(self, unit_paise: int) -> int:
        price = Fraction(unit_paise)
        if self.compare_incl_gst and not self.gst_included:
            price *= Fraction(10_000 + self.gst_bp, 10_000)
        elif not self.compare_incl_gst and self.gst_included:
            price /= Fraction(10_000 + self.gst_bp, 10_000)
        price += Fraction((self.freight_paise + self.unloading_paise) * 1000, self.qty_milli)
        return div_round_half_up(price.numerator, price.denominator)

    def unit_for_landed(self, landed_paise: int) -> int:
        """The quoted unit price (whole rupees, rounded up) that lands at >= landed_paise."""
        price = Fraction(landed_paise) - Fraction(
            (self.freight_paise + self.unloading_paise) * 1000, self.qty_milli
        )
        if self.compare_incl_gst and not self.gst_included:
            price /= Fraction(10_000 + self.gst_bp, 10_000)
        elif not self.compare_incl_gst and self.gst_included:
            price *= Fraction(10_000 + self.gst_bp, 10_000)
        rupees = -(-price.numerator // (price.denominator * 100))  # ceil to whole rupees
        return max(rupees, 1) * 100


@dataclass(frozen=True)
class Counter:
    kind: str  # "match_benchmark" | "improve" | "best_and_final" | "hold"
    landed_paise: int | None  # the landed target behind the ask (None = no number)
    unit_paise: int | None  # the same target in the vendor's quoted terms
    lower_offer_exists: bool  # true only when a real, lower landed offer exists


def floor_paise(reference_median_landed: int | None, fallback: int) -> int:
    base = reference_median_landed or fallback
    return div_round_half_up(base * FLOOR_BP, 10_000)


def next_counter(
    *,
    round_no: int,
    vendor_landed: int,
    benchmark_landed: int,
    floor: int,
    terms: VendorTerms,
    max_rounds: int = 3,
) -> Counter:
    """What to ask this vendor for in `round_no` (1-based)."""
    lower_exists = benchmark_landed < vendor_landed
    if round_no >= max_rounds:
        return Counter("best_and_final", None, None, lower_exists)
    if lower_exists:
        target = max(benchmark_landed, floor)  # match (or beat) the best real offer
        kind = "match_benchmark"
    else:
        ask_bp = BENCHMARK_ASK_BP.get(round_no, 200)
        target = max(div_round_half_up(vendor_landed * (10_000 - ask_bp), 10_000), floor)
        kind = "improve"
    if target >= vendor_landed:
        return Counter("hold", None, None, lower_exists)  # already at the floor: nothing to ask
    unit = terms.unit_for_landed(target)
    if terms.landed(unit) >= vendor_landed:
        return Counter("hold", None, None, lower_exists)  # rupee rounding ate the whole ask
    return Counter(kind, terms.landed(unit), unit, lower_exists)


def reached_target(vendor_landed: int, target_paise: int | None) -> bool:
    return target_paise is not None and vendor_landed <= target_paise
