"""Number validator for LLM-written messages (PROMPT.md 10.5).

Every number in the generated text must be one we explicitly allowed (the counter
price, the quantity, dates, the RFQ code). One unexpected number rejects the text."""

import re

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """Normalised numbers: commas dropped, trailing '.00' dropped."""
    out = []
    for raw in _NUMBER.findall(text):
        n = raw.replace(",", "").rstrip(".")
        if n.endswith(".00"):
            n = n[:-3]
        elif "." in n:
            n = n.rstrip("0").rstrip(".")
        out.append(n.lstrip("0") or "0")
    return out


def allowed_set(*values: str) -> set[str]:
    allowed: set[str] = set()
    for v in values:
        allowed.update(numbers_in(v))
    return allowed


def unexpected_numbers(text: str, allowed: set[str]) -> list[str]:
    return [n for n in numbers_in(text) if n not in allowed]
