"""Money is integer paise everywhere. Rupees exist only at the display edge."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

BP_DENOM = 10_000  # basis points: 18% GST = 1800


def div_round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounding halves away from zero (so -2.5 -> -3, 2.5 -> 3)."""
    if denominator == 0:
        raise ZeroDivisionError("denominator is zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    q, r = divmod(abs(numerator), denominator)
    if 2 * r >= denominator:
        q += 1
    return q if numerator >= 0 else -q


def apply_bp(paise: int, bp: int) -> int:
    """paise * bp / 10000, rounded half up. apply_bp(38000, 1800) == 6840."""
    return div_round_half_up(paise * bp, BP_DENOM)


def rupees_to_paise(value: str | int | Decimal) -> int:
    """Parse '11,400.50', '₹380', 380, Decimal('380.005') into paise (half up)."""
    if isinstance(value, int):
        return value * 100
    text = str(value).replace("₹", "").replace(",", "").replace("Rs.", "").strip()
    try:
        amount = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"not a rupee amount: {value!r}") from e
    if not amount.is_finite():
        raise ValueError(f"not a rupee amount: {value!r}")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _indian_group(digits: str) -> str:
    """'1140000' -> '11,40,000' (last three, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail])


def format_inr(paise: int) -> str:
    """1140000 -> '₹11,400.00'; 50000000 -> '₹5,00,000.00'; -150 -> '-₹1.50'."""
    sign = "-" if paise < 0 else ""
    rupees, p = divmod(abs(paise), 100)
    return f"{sign}₹{_indian_group(str(rupees))}.{p:02d}"
