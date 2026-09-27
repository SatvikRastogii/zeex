import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str) -> str:
    """'+91 90000 10001', '9000010001', '919000010001' -> '+919000010001'."""
    digits = re.sub(r"[\s\-()]", "", raw.strip())
    if re.fullmatch(r"\d{10}", digits):
        digits = "+91" + digits
    elif re.fullmatch(r"91\d{10}", digits):
        digits = "+" + digits
    if not E164.fullmatch(digits):
        raise ValueError("invalid phone number")
    return digits
