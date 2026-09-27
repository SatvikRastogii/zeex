"""Per-builder settings defaults (stored in builder_orgs.settings, editable by the owner)."""

from typing import Any

DEFAULT_WEIGHTS = {"price": 50, "delivery": 20, "payment": 10, "reliability": 10, "quality": 10}

DEFAULT_ORG_SETTINGS: dict[str, Any] = {
    "weights": DEFAULT_WEIGHTS,
    "gst_mode": "incl",  # compare landed cost including GST
    # First ask: "We have received a lower offer". Repeat ask: exact lower price, no names.
    "disclosure": "lower_offer_then_price",
    "approval_limits_paise": {"purchase_manager": 50_000_000},  # ₹5,00,000
    "working_hours": {"start": "09:00", "end": "20:00"},
    "bid_window_hours": 24,
    "max_rounds": 3,
    "shortlist_size": 3,
    "match_top_n": 5,
    "po_confirm_working_hours": 4,
    "reply_timeout_working_hours": 3,
}


def org_settings(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Stored settings over defaults (so new keys get defaults on old orgs)."""
    return {**DEFAULT_ORG_SETTINGS, **(stored or {})}
