import re

# 2-digit state code, 10-char PAN (5 letters, 4 digits, 1 letter), entity no., 'Z', check char.
GSTIN_RE = re.compile(r"^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def is_valid_gstin(value: str | None) -> bool:
    """Format check only (the checksum is not verified)."""
    return bool(value) and GSTIN_RE.fullmatch(value or "") is not None
