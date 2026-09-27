"""Deterministic reading of a negotiation reply (English and Hinglish).

Fallback when the LLM fails, and the mock provider's behaviour. Conservative:
anything it cannot classify is `unclear`, which a human will see after two tries."""

import re

from app.domain.quote_text import RATE_ONLY, find_prices
from app.llm.schemas import ParsedReply

WANTS_CALL = re.compile(
    r"\b(call me|call karo|phone karo|phone pe baat|baat karte|talk on (?:the )?phone|give me a call|call back|ring me)\b",
    re.I,
)
ABUSIVE = re.compile(
    r"\b(idiot|stupid|bloody|nonsense|bakwas|pagal|chutiya|bewakoof|shut up|get lost)\b", re.I
)
ASKS_PRICE = re.compile(
    r"(?:what|which|kya|kitna|kitne|kaun sa|konsa|kis)\b.{0,30}\b(?:lower|kam|best|other|dusra|doosra|rate|price|offer|quote)"
    r"|(?:lower|kam|other|dusra|doosra) (?:rate|price|offer) (?:kya|kitna|what)"
    r"|who (?:is|gave|quoted)|kisne diya|show me the (?:offer|rate)",
    re.I,
)
ACCEPT = re.compile(
    r"^\W*(ok+|okay|done|deal|agreed|accepted|fine|theek hai|thik hai|chalega|haan|ha|yes|confirm(?:ed)?)\b",
    re.I,
)
REJECT = re.compile(
    r"\b(not possible|no discount|can'?t|cannot|nahi|nahin|no way|final hai|last rate|nahi ho payega|won'?t|not interested)\b",
    re.I,
)
QUESTION = re.compile(r"\?|\b(what|why|when|which|how|kya|kyun|kab|kaise|kaunsa)\b", re.I)
TERM_CHANGES = [
    (
        "delivery_date",
        re.compile(
            r"\b(deliver\w*|supply|dispatch)\b.{0,20}\b(after|later|next week|delay|by \d|on \d|din baad|days later)",
            re.I,
        ),
    ),
    (
        "brand",
        re.compile(r"\b(other|different|another|dusra) brand\b|\bbrand (?:change|badal)", re.I),
    ),
    (
        "payment_terms",
        re.compile(r"\b(credit|payment terms?|udhaar)\b.{0,20}\b(\d+ days?|reduce|kam|less)", re.I),
    ),
    ("advance", re.compile(r"\b(advance|pehle payment|full payment first)\b", re.I)),
    (
        "quantity",
        re.compile(r"\b(only|sirf|max(?:imum)?)\s*\d+\s*(bags?|tonnes?|mt|nos|cft)\b", re.I),
    ),
]


def parse_reply_text(text: str) -> ParsedReply:
    t = " ".join(text.split())
    fields: dict[str, object] = {}
    prices = find_prices(t)
    if prices:
        fields["price"], fields["per_unit"] = prices[0][1], prices[0][2]
    elif m := RATE_ONLY.search(t):
        fields["price"] = m.group(1)
    elif m2 := re.fullmatch(r"\D{0,20}?(\d{2,6})(?:/-)?\D{0,30}", t):
        fields["price"] = m2.group(
            1
        )  # "375 final", "chalo 372" : a bare number is a price in reply to a counter
    fields["wants_call"] = bool(WANTS_CALL.search(t))
    fields["abusive"] = bool(ABUSIVE.search(t))
    fields["asks_competitor_price"] = bool(ASKS_PRICE.search(t))
    fields["term_changes"] = [name for name, rx in TERM_CHANGES if rx.search(t)]

    if fields["asks_competitor_price"]:
        intent = "question"
    elif "price" in fields:
        intent = (
            "accept" if ACCEPT.search(t) and not REJECT.search(t) and len(t) < 40 else "counter"
        )
    elif ACCEPT.search(t):
        intent = "accept"
    elif REJECT.search(t):
        intent = "reject"
    elif QUESTION.search(t) or fields["wants_call"]:
        intent = "question"
    else:
        intent = "unclear"
    fields["intent"] = intent
    return ParsedReply.model_validate(fields)
