"""Every permission row in PROMPT.md section 11, at the matrix and endpoint level."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.permissions import PERMISSIONS, can
from app.db.models import User
from app.domain.settings import DEFAULT_ORG_SETTINGS
from tests.phones import (
    ADMIN,
    SHARMA_OWNER,
    SHARMA_PM,
    SHARMA_SE,
    VENDOR_BALAJI,
)

Login = Callable[[str], TestClient]

# Written out from the spec table, independently of app/auth/permissions.py.
SPEC = {
    "bom.create": {"owner": True, "purchase_manager": True, "site_engineer": True},
    "shortlist.edit": {"owner": True, "purchase_manager": True, "site_engineer": False},
    "negotiation.take_over": {"owner": True, "purchase_manager": True, "site_engineer": False},
    "award.approve": {"owner": True, "purchase_manager": True, "site_engineer": False},
    "delivery.confirm": {"owner": True, "purchase_manager": True, "site_engineer": True},
    "settings.edit": {"owner": True, "purchase_manager": False, "site_engineer": False},
    "users.manage": {"owner": True, "purchase_manager": False, "site_engineer": False},
}


def test_matrix_covers_every_permission() -> None:
    assert set(SPEC) == set(PERMISSIONS)


@pytest.mark.parametrize(
    ("perm", "role", "allowed"),
    [(p, r, a) for p, roles in SPEC.items() for r, a in roles.items()],
)
def test_permission_matrix(perm: str, role: str, allowed: bool) -> None:
    assert can(role, perm) is allowed


def settings_body(**over: Any) -> dict[str, Any]:
    return {**DEFAULT_ORG_SETTINGS, **over}


@pytest.mark.parametrize(
    ("phone", "status"), [(SHARMA_OWNER, 200), (SHARMA_PM, 403), (SHARMA_SE, 403)]
)
def test_settings_edit_endpoint(login: Login, phone: str, status: int) -> None:
    r = login(phone).put("/api/org/settings", json=settings_body())
    assert r.status_code == status


@pytest.mark.parametrize(
    ("phone", "status"), [(SHARMA_OWNER, 200), (SHARMA_PM, 403), (SHARMA_SE, 403)]
)
def test_users_manage_endpoint(login: Login, phone: str, status: int) -> None:
    assert login(phone).get("/api/org/users").status_code == status


@pytest.mark.parametrize(
    "weights",
    [
        {"price": 50, "delivery": 20, "payment": 10, "reliability": 10, "quality": 5},
        {"price": 60, "delivery": 20, "payment": 10, "reliability": 10, "quality": 10},
        {"price": 50, "delivery": 20, "payment": 10, "reliability": 20},
        {"price": 110, "delivery": -10, "payment": 0, "reliability": 0, "quality": 0},
    ],
)
def test_weights_must_sum_to_100(login: Login, weights: dict[str, int]) -> None:
    r = login(SHARMA_OWNER).put("/api/org/settings", json=settings_body(weights=weights))
    assert r.status_code == 422


def test_settings_saved(login: Login) -> None:
    c = login(SHARMA_OWNER)
    w = {"price": 40, "delivery": 30, "payment": 10, "reliability": 10, "quality": 10}
    assert c.put("/api/org/settings", json=settings_body(weights=w)).status_code == 200
    assert c.get("/api/org").json()["settings"]["weights"] == w


def test_working_hours_must_be_ordered(login: Login) -> None:
    body = settings_body(working_hours={"start": "20:00", "end": "09:00"})
    assert login(SHARMA_OWNER).put("/api/org/settings", json=body).status_code == 422


def test_owner_creates_and_deactivates_user(login: Login) -> None:
    c = login(SHARMA_OWNER)
    r = c.post(
        "/api/org/users",
        json={"phone": "+919000010009", "name": "New PM", "role": "purchase_manager",
              "approval_limit_paise": 10_000_000},
    )  # fmt: skip
    assert r.status_code == 201
    uid = r.json()["id"]
    assert c.patch(f"/api/org/users/{uid}", json={"is_active": False}).json()["is_active"] is False


def test_duplicate_phone_rejected(login: Login) -> None:
    r = login(SHARMA_OWNER).post(
        "/api/org/users", json={"phone": SHARMA_PM, "name": "Dup", "role": "site_engineer"}
    )
    assert r.status_code == 409


def test_owner_cannot_demote_or_deactivate_self(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    me = seeded.scalars(select(User).where(User.phone == SHARMA_OWNER)).one()
    assert c.patch(f"/api/org/users/{me.id}", json={"role": "site_engineer"}).status_code == 400
    assert c.patch(f"/api/org/users/{me.id}", json={"is_active": False}).status_code == 400


def test_pm_cannot_create_users(login: Login) -> None:
    r = login(SHARMA_PM).post(
        "/api/org/users", json={"phone": "+919000010008", "name": "X", "role": "owner"}
    )
    assert r.status_code == 403


def test_vendor_cannot_use_builder_endpoints(login: Login) -> None:
    c = login(VENDOR_BALAJI)
    assert c.get("/api/org").status_code == 403
    assert c.get("/api/sites").status_code == 403


def test_builder_cannot_use_vendor_endpoints(login: Login) -> None:
    assert login(SHARMA_OWNER).get("/api/vendor/messages").status_code == 403


@pytest.mark.parametrize(
    ("phone", "status"), [(ADMIN, 200), (SHARMA_OWNER, 403), (VENDOR_BALAJI, 403)]
)
def test_admin_endpoints_admin_only(login: Login, phone: str, status: int) -> None:
    assert login(phone).get("/api/admin/clock").status_code == status


def test_admin_is_not_a_builder(login: Login) -> None:
    assert login(ADMIN).get("/api/org").status_code == 403
