import pytest

from app.domain.money import apply_bp, div_round_half_up, format_inr, rupees_to_paise


@pytest.mark.parametrize(
    ("n", "d", "want"),
    [(5, 2, 3), (-5, 2, -3), (4, 3, 1), (5, 3, 2), (-4, 3, -1), (7, -2, -4), (0, 7, 0), (1, 2, 1)],
)
def test_div_round_half_up(n: int, d: int, want: int) -> None:
    assert div_round_half_up(n, d) == want


def test_div_by_zero() -> None:
    with pytest.raises(ZeroDivisionError):
        div_round_half_up(1, 0)


def test_apply_bp_gst() -> None:
    assert apply_bp(38000, 1800) == 6840  # 18% of ₹380
    assert apply_bp(1, 5000) == 1  # half a paisa rounds up
    assert apply_bp(3, 1800) == 1  # 0.54 -> 1


@pytest.mark.parametrize(
    ("raw", "paise"),
    [
        ("11,400.50", 1140050),
        ("₹380", 38000),
        (380, 38000),
        ("380.005", 38001),
        ("380.004", 38000),
        ("Rs. 1,00,000", 10000000),
        ("0", 0),
    ],
)
def test_rupees_to_paise(raw: str | int, paise: int) -> None:
    assert rupees_to_paise(raw) == paise


@pytest.mark.parametrize("bad", ["abc", "", "NaN", "Infinity", "1.2.3"])
def test_rupees_to_paise_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        rupees_to_paise(bad)


@pytest.mark.parametrize(
    ("paise", "text"),
    [
        (1140000, "₹11,400.00"),
        (50000000, "₹5,00,000.00"),
        (0, "₹0.00"),
        (5, "₹0.05"),
        (99999, "₹999.99"),
        (100000, "₹1,000.00"),
        (1234567890, "₹1,23,45,678.90"),
        (-150, "-₹1.50"),
    ],
)
def test_format_inr_indian_grouping(paise: int, text: str) -> None:
    assert format_inr(paise) == text
