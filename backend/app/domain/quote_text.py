"""Deterministic quote extraction from plain text (English and Hinglish).

Used as the fallback when the LLM fails, and by the mock provider. It only reads
numbers that are written next to a unit or keyword; it never guesses."""

import re
from datetime import date, datetime, timedelta

from app.llm.schemas import ParsedQuote, RateLine

_UNIT_WORDS = {
    "bag": "bag", "bags": "bag", "bori": "bag", "katta": "bag",
    "tonne": "tonne", "tonnes": "tonne", "ton": "tonne", "tons": "tonne", "mt": "tonne",
    "kg": "kg", "kilo": "kg",
    "cft": "cft", "brass": "brass",
    "nos": "nos", "no": "nos", "pcs": "nos", "piece": "nos", "brick": "nos", "bricks": "nos", "block": "nos",
    "box": "box", "boxes": "box",
}  # fmt: skip
_UNIT_RE = "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))
_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_RS = r"(?:₹|rs\.?|inr|rupees?)?"

# "380 per bag", "₹380/bag", "Rs 7,600 per MT", "380 rs bag", "380 ka rate bag ka"
_CONNECTOR = r"(?:/|per|each|prati|ka rate hai(?: ek)?|ka rate)"
_RS_REQ = r"(?:₹|rs\.?|inr|rupees?)"
_RS_AFTER = r"(?:/-|rs\.?|rupees?|rupaye)"
# A price needs money context: a currency marker or a price connector. A bare
# "30 bags" or "50 kg" is a quantity or pack size, not a rate.
PRICE_PATTERNS = [
    # "Rs 7,600 per MT", "₹380/bag", "Rs 395 bag"
    re.compile(rf"{_RS_REQ}\s*{_NUM}\s*(?:/-)?\s*{_CONNECTOR}?\s*\b({_UNIT_RE})\b", re.I),
    # "380 per bag", "380/bag", "380 ka rate hai bag"
    re.compile(rf"(?<![\d.,]){_NUM}\s*{_RS_AFTER}?\s*{_CONNECTOR}\s*\b({_UNIT_RE})\b", re.I),
    # "395 rs bag", "372/- bag"
    re.compile(rf"(?<![\d.,]){_NUM}\s*{_RS_AFTER}\s*\b({_UNIT_RE})\b", re.I),
]


def find_prices(text: str) -> list[tuple[int, str, str]]:
    """(position, amount, unit) for every price in the text, in reading order."""
    hits: dict[int, tuple[int, int, str, str]] = {}
    for pat in PRICE_PATTERNS:
        for m in pat.finditer(text):
            start = m.start(1)
            if all(not (a <= start < b) for a, b, _, _ in hits.values()):
                hits[start] = (start, m.end(), _amount(m.group(1)), _UNIT_WORDS[m.group(2).lower()])
    return [(s, amt, unit) for s, _, amt, unit in sorted(hits.values())]


# "rate 380", "rate: ₹380", "rate hai 380"
RATE_ONLY = re.compile(rf"\brate\b\s*(?:hai|is|:|-)?\s*{_RS}\s*{_NUM}", re.I)
GST_EXCL = re.compile(
    r"\+\s*gst|gst\s*(?:extra|alag|excl|excluded|separate|additional)|excl\w*\.?\s*(?:of\s*)?gst|plus\s*gst",
    re.I,
)
GST_INCL = re.compile(
    r"(?:incl\w*\.?|including|inclusive|with|sahit)\s*(?:of\s*)?gst|gst\s*(?:incl\w*|included|sahit|inclusive)",
    re.I,
)
GST_PCT = re.compile(
    r"gst\s*@?\s*(\d{1,2}(?:\.\d{1,2})?)\s*%|(\d{1,2}(?:\.\d{1,2})?)\s*%\s*gst", re.I
)
FREIGHT_AMT = re.compile(
    rf"(?:freight|transport|bhada|delivery charges?)\s*(?:charges?)?\s*(?::|-|rs\.?|₹)?\s*{_NUM}",
    re.I,
)
FREIGHT_FREE = re.compile(
    r"free delivery|freight\s*(?:free|included|incl\w*)|incl\w*\.?\s*freight|delivered price|door delivery free",
    re.I,
)
UNLOADING = re.compile(rf"unloading\s*(?:charges?)?\s*(?::|-|rs\.?|₹)?\s*{_NUM}", re.I)
TOTAL = re.compile(rf"\b(?:total|grand total|amount)\b\s*(?::|-)?\s*{_RS}\s*{_NUM}", re.I)
QTY_OFFER = re.compile(
    rf"(?:can supply|available|supply|stock)\s*(?::|-)?\s*{_NUM}\s*({_UNIT_RE})\b|{_NUM}\s*({_UNIT_RE})\s*(?:available|in stock|max)",
    re.I,
)
CREDIT = re.compile(
    r"(\d{1,3})\s*days?\s*(?:credit|payment|udhaar)|credit\s*(?:of|:|-)?\s*(\d{1,3})\s*days?", re.I
)
ADVANCE = re.compile(r"\b(?:advance|cash on delivery|cod|full payment before)\b", re.I)
RFQ_CODE = re.compile(r"RFQ-\d{4}-\d{5}-\d{2}")
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_TXT = rf"(\d{{1,2}}(?:st|nd|rd|th)?\s*(?:{_MONTHS})[a-z]*\.?(?:\s*,?\s*\d{{4}})?|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?)"
DELIVERY = re.compile(
    rf"(?:deliver\w*|supply|dispatch)\s*(?:by|on|before|till|:)?\s*{DATE_TXT}", re.I
)
DELIVERY_DAYS = re.compile(
    r"(?:deliver\w*|supply|dispatch)\s*(?:in|within)\s*(\d{1,2})\s*(?:days?|din)"
    r"|(\d{1,2})\s*(?:days?|din)\s*(?:me|mein|main|में)\s*(?:deliver\w*|supply|dispatch)",
    re.I,
)
VALIDITY = re.compile(
    rf"valid\w*\s*(?:till|until|upto|up to|to|:)?\s*{DATE_TXT}|valid\w*\s*(?:for)?\s*(\d{{1,3}})\s*days?",
    re.I,
)


def _amount(text: str) -> str:
    return text.replace(",", "")


def parse_date(text: str, today: date) -> date | None:
    t = re.sub(r"(st|nd|rd|th)\b", "", text.strip().lower().rstrip(".")).replace(",", " ")
    t = " ".join(t.split())
    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(t.replace("sept", "sep"), fmt).date()
        except ValueError:
            pass
    for fmt in ("%d %b", "%d %B", "%d/%m", "%d-%m"):
        try:
            d = datetime.strptime(t.replace("sept", "sep"), fmt).date().replace(year=today.year)
        except ValueError:
            continue
        return d if d >= today - timedelta(days=30) else d.replace(year=today.year + 1)
    return None


def _date_or_days(m: re.Match[str] | None, today: date) -> date | None:
    if m is None:
        return None
    if m.group(1):
        return parse_date(m.group(1), today)
    return today + timedelta(days=int(m.group(2)))


def parse_text(text: str, today: date, brands: list[str] | None = None) -> ParsedQuote:
    """Everything we can read reliably from free text. Missing fields stay None."""
    fields: dict[str, object] = {}
    found = 0

    prices = find_prices(text)
    lines = []
    for raw_line in text.splitlines():
        found_in_line = find_prices(raw_line)
        if found_in_line:
            pos, amount, unit = found_in_line[0]
            item = re.sub(r"(?i)\b(?:rate|price|rs\.?|₹)\s*[:=-]?\s*$", "", raw_line[:pos]).strip(
                " :-\t|"
            )
            if item and not re.fullmatch(r"(?i)(rate|price|our rate|hamara rate)", item):
                lines.append(RateLine(item_text=item[:60], unit_price=amount, price_unit=unit))
    if len(lines) >= 2:
        fields["lines"] = lines
        found += 1
    elif prices:
        _, amount, unit = prices[0]
        fields["unit_price"], fields["price_unit"] = amount, unit
        found += 2
    elif rm := RATE_ONLY.search(text):
        fields["unit_price"] = _amount(rm.group(1))
        found += 1

    if GST_INCL.search(text):
        fields["gst_included"] = True
    elif GST_EXCL.search(text):
        fields["gst_included"] = False
    if gm := GST_PCT.search(text):
        fields["gst_percent"] = gm.group(1) or gm.group(2)
    if FREIGHT_FREE.search(text):
        fields["freight_included"] = True
    elif fm := FREIGHT_AMT.search(text):
        fields["freight"], fields["freight_included"] = _amount(fm.group(1)), False
    if um := UNLOADING.search(text):
        fields["unloading"] = _amount(um.group(1))
    if tm := TOTAL.search(text):
        fields["stated_total"] = _amount(tm.group(1))
    if qm := QTY_OFFER.search(text):
        qty, unit = (qm.group(1), qm.group(2)) if qm.group(1) else (qm.group(3), qm.group(4))
        fields["qty_offered"], fields["qty_unit"] = _amount(qty), _UNIT_WORDS[unit.lower()]
    if cm := CREDIT.search(text):
        fields["payment_terms_days"] = int(cm.group(1) or cm.group(2))
    elif ADVANCE.search(text):
        fields["payment_terms_days"] = 0
    days = DELIVERY_DAYS.search(text)
    if d := _date_or_days(DELIVERY.search(text), today):
        fields["delivery_date"] = d
        found += 1
    elif days:
        fields["delivery_date"] = today + timedelta(days=int(days.group(1) or days.group(2)))
        found += 1
    if v := _date_or_days(VALIDITY.search(text), today):
        fields["validity_until"] = v
    if code := RFQ_CODE.search(text):
        fields["rfq_code"] = code.group(0)
    for b in brands or []:
        if re.search(rf"\b{re.escape(b)}\b", text, re.I):
            fields["brand"] = b
            break
    fields["confidence"] = min(95, 40 + 20 * found)
    return ParsedQuote.model_validate(fields)
