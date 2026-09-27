"""Builder-side permission matrix (PROMPT.md section 11)."""

BUILDER_ROLES = ("owner", "purchase_manager", "site_engineer")

PERMISSIONS: dict[str, frozenset[str]] = {
    "bom.create": frozenset({"owner", "purchase_manager", "site_engineer"}),
    "shortlist.edit": frozenset({"owner", "purchase_manager"}),
    "negotiation.take_over": frozenset({"owner", "purchase_manager"}),
    "award.approve": frozenset({"owner", "purchase_manager"}),  # PM up to their limit
    "delivery.confirm": frozenset({"owner", "purchase_manager", "site_engineer"}),
    "settings.edit": frozenset({"owner"}),
    "users.manage": frozenset({"owner"}),
}


def can(role: str, permission: str) -> bool:
    return role in PERMISSIONS[permission]
