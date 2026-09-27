"""Strict output schemas for everything the LLM returns.

No field is free text that could carry an instruction: amounts are digit strings,
units and intents are enums, dates are dates, names are short. `extra="forbid"`
rejects anything else the model adds."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Amount = Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,2})?$")]  # rupees, no commas
Unit = Literal["bag", "tonne", "kg", "cft", "brass", "nos", "box"]
Short = Annotated[str, Field(max_length=60)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RateLine(_Strict):
    item_text: Short
    unit_price: Amount
    price_unit: Unit


class ParsedQuote(_Strict):
    """One vendor quotation. Rate lists come back as `lines`; code picks the RFQ item."""

    rfq_code: Annotated[str, Field(pattern=r"^RFQ-\d{4}-\d{5}-\d{2}$")] | None = None
    unit_price: Amount | None = None
    price_unit: Unit | None = None
    gst_included: bool | None = None
    gst_percent: Annotated[str, Field(pattern=r"^\d{1,2}(\.\d{1,2})?$")] | None = None
    freight: Amount | None = None
    freight_included: bool | None = None
    unloading: Amount | None = None
    qty_offered: Annotated[str, Field(pattern=r"^\d{1,9}(\.\d{1,3})?$")] | None = None
    qty_unit: Unit | None = None
    stated_total: Amount | None = None
    delivery_date: date | None = None
    validity_until: date | None = None
    payment_terms_days: Annotated[int, Field(ge=0, le=365)] | None = None
    brand: Short | None = None
    lines: list[RateLine] = Field(default_factory=list, max_length=100)
    confidence: Annotated[int, Field(ge=0, le=100)] = 50


class ParsedReply(_Strict):
    """A vendor's reply during negotiation (Stage 9)."""

    intent: Literal["accept", "counter", "reject", "question", "unclear"]
    price: Amount | None = None
    per_unit: Unit | None = None
    conditions: list[Short] = Field(default_factory=list, max_length=10)
    term_changes: list[
        Literal["delivery_date", "brand", "payment_terms", "advance", "quantity"]
    ] = Field(default_factory=list)
    wants_call: bool = False
    asks_competitor_price: bool = False  # "what is the lower rate?"
    abusive: bool = False
