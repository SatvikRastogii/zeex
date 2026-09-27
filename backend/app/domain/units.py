"""Exact unit conversion. Quantities are stored as integer milli-units of the
item's canonical unit (30 bags -> 30_000), so fractional tonnes stay exact."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

MILLI = 1000


@dataclass(frozen=True)
class Conversion:
    """1 from_unit == factor to_unit."""

    from_unit: str
    to_unit: str
    factor: Fraction


class UnitError(ValueError):
    pass


UNIT_ALIASES = {
    "bag": "bag", "bags": "bag", "bori": "bag",
    "tonne": "tonne", "tonnes": "tonne", "ton": "tonne", "tons": "tonne",
    "mt": "tonne", "t": "tonne",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "cft": "cft", "cu ft": "cft", "cubic feet": "cft", "cuft": "cft",
    "brass": "brass",
    "nos": "nos", "no": "nos", "pcs": "nos", "pc": "nos", "piece": "nos",
    "pieces": "nos", "units": "nos",
    "box": "box", "boxes": "box",
}  # fmt: skip


def normalize_unit(raw: str) -> str:
    key = " ".join(raw.strip().lower().rstrip(".").split())
    if key not in UNIT_ALIASES:
        raise UnitError(f"unknown unit: {raw!r}")
    return UNIT_ALIASES[key]


def conversion_factor(from_unit: str, to_unit: str, conversions: Iterable[Conversion]) -> Fraction:
    """Factor f such that 1 from_unit == f to_unit, walking conversions both ways."""
    if from_unit == to_unit:
        return Fraction(1)
    graph: dict[str, list[tuple[str, Fraction]]] = {}
    for c in conversions:
        graph.setdefault(c.from_unit, []).append((c.to_unit, c.factor))
        graph.setdefault(c.to_unit, []).append((c.from_unit, 1 / c.factor))
    seen = {from_unit}
    queue: deque[tuple[str, Fraction]] = deque([(from_unit, Fraction(1))])
    while queue:
        unit, acc = queue.popleft()
        for nxt, f in graph.get(unit, []):
            if nxt == to_unit:
                return acc * f
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, acc * f))
    raise UnitError(f"cannot convert {from_unit} to {to_unit}")


def to_canonical_milli(
    qty: Fraction, unit: str, canonical_unit: str, conversions: Iterable[Conversion]
) -> int:
    """Convert qty in unit into integer milli-units of canonical_unit. Must be exact."""
    milli = qty * conversion_factor(unit, canonical_unit, conversions) * MILLI
    if milli.denominator != 1:
        raise UnitError(f"{qty} {unit} is not a whole number of milli-{canonical_unit}")
    return int(milli)


def price_per_canonical_paise(
    price_paise: int, price_unit: str, canonical_unit: str, conversions: Iterable[Conversion]
) -> Fraction:
    """A price per price_unit restated per canonical unit (exact; round at the end)."""
    # ₹P per tonne, canonical bag: 1 bag = 1/20 tonne, so ₹P/20 per bag.
    return price_paise * conversion_factor(canonical_unit, price_unit, conversions)
