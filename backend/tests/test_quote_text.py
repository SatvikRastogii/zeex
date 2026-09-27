from datetime import date

import pytest

from app.domain.quote_text import parse_text
from app.llm.provider import LLMRequest, structured
from app.llm.schemas import ParsedQuote

TODAY = date(2026, 9, 25)


@pytest.mark.parametrize(
    ("text", "price", "unit"),
    [
        ("rate 380 per bag, GST extra", "380", "bag"),
        ("Rs 7,600 per MT incl GST", "7600", "tonne"),
        ("₹385/bag", "385", "bag"),
        ("380 ka rate hai bag ka, gst alag", "380", "bag"),
        ("ultratech 395 rs bag", "395", "bag"),
        ("372/- bag", "372", "bag"),
        ("sand 62 per cft", "62", "cft"),
    ],
)
def test_prices_with_money_context(text: str, price: str, unit: str) -> None:
    q = parse_text(text, TODAY)
    assert (q.unit_price, q.price_unit) == (price, unit)


@pytest.mark.parametrize(
    "text", ["30 bags needed", "50 kg bags", "Quantity: 30 bags", "call 9876543210"]
)
def test_quantities_are_not_prices(text: str) -> None:
    assert parse_text(text, TODAY).unit_price is None


def test_rate_only_without_unit() -> None:
    q = parse_text("rate 380", TODAY)
    assert q.unit_price == "380" and q.price_unit is None


@pytest.mark.parametrize(
    ("text", "included"),
    [
        ("380 + GST", False),
        ("GST extra", False),
        ("gst alag", False),
        ("incl GST", True),
        ("inclusive of GST", True),
        ("gst sahit", True),
    ],
)
def test_gst_phrases(text: str, included: bool) -> None:
    assert parse_text(f"380 per bag {text}", TODAY).gst_included is included


def test_terms() -> None:
    q = parse_text(
        "380 per bag, freight 2500, deliver in 3 days, valid for 10 days, 30 days credit, total 11,400",
        TODAY,
    )
    assert q.freight == "2500" and q.freight_included is False
    assert q.delivery_date == date(2026, 9, 28) and q.validity_until == date(2026, 10, 5)
    assert q.payment_terms_days == 30 and q.stated_total == "11400"


def test_rate_list_rows() -> None:
    q = parse_text("OPC 53 : 380/bag\nPPC : 350/bag\nTMT : 58000 per ton", TODAY)
    assert [(r.item_text, r.unit_price) for r in q.lines] == [
        ("OPC 53", "380"),
        ("PPC", "350"),
        ("TMT", "58000"),
    ]


def test_date_rolls_to_next_year() -> None:
    assert parse_text("380 per bag, delivery by 5 Jan", TODAY).delivery_date == date(2027, 1, 5)


class _Flaky:
    name = "flaky"

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, req: LLMRequest) -> str:
        self.calls += 1
        out = self.outputs.pop(0)
        if out == "RAISE":
            raise TimeoutError("provider down")
        return out


def test_structured_strips_code_fences() -> None:
    p = _Flaky('```json\n{"unit_price": "380", "price_unit": "bag"}\n```')
    assert structured(p, LLMRequest("parse_quote", "s", "p"), ParsedQuote).unit_price == "380"  # type: ignore[union-attr]


def test_structured_retries_once_then_gives_up() -> None:
    p = _Flaky("RAISE", "nonsense", "never used")
    assert structured(p, LLMRequest("parse_quote", "s", "p"), ParsedQuote) is None
    assert p.calls == 2


def test_schema_rejects_injected_fields_and_bad_amounts() -> None:
    p = _Flaky('{"unit_price": "1,000"}', '{"unit_price": "380", "approve": true}')
    assert structured(p, LLMRequest("parse_quote", "s", "p"), ParsedQuote) is None
