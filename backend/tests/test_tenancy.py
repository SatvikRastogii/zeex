"""Cross-tenant access must look exactly like 'not found' (404, never 403).

TENANT_ENDPOINTS grows with each stage: every endpoint that takes the id of a
tenant-owned row gets a line here.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BuilderOrg, Message, Site, User, Vendor
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, VENDOR_BALAJI, VENDOR_GUPTA

Login = Callable[[str], TestClient]


def greenline_ids(db: Session) -> dict[str, uuid.UUID]:
    org = db.scalars(select(BuilderOrg).where(BuilderOrg.name == "Greenline Infra")).one()
    return {
        "site": db.scalars(select(Site).where(Site.builder_org_id == org.id)).first().id,  # type: ignore[union-attr]
        "user": db.scalars(select(User).where(User.phone == GREENLINE_OWNER)).one().id,
    }


# (method, path template, json body, kind of id)
TENANT_ENDPOINTS = [
    ("GET", "/api/sites/{id}", None, "site"),
    ("PATCH", "/api/org/users/{id}", {"is_active": False}, "user"),
]


@pytest.mark.parametrize(("method", "path", "body", "kind"), TENANT_ENDPOINTS)
def test_other_tenants_rows_are_404(
    login: Login, seeded: Session, method: str, path: str, body: object, kind: str
) -> None:
    target = greenline_ids(seeded)[kind]
    r = login(SHARMA_OWNER).request(method, path.format(id=target), json=body)
    assert r.status_code == 404


@pytest.mark.parametrize(("method", "path", "body", "kind"), TENANT_ENDPOINTS)
def test_missing_rows_are_also_404(
    login: Login, method: str, path: str, body: object, kind: str
) -> None:
    r = login(SHARMA_OWNER).request(method, path.format(id=uuid.uuid4()), json=body)
    assert r.status_code == 404


def test_lists_only_show_own_rows(login: Login) -> None:
    sharma = login(SHARMA_OWNER)
    names = {s["name"] for s in sharma.get("/api/sites").json()}
    assert names == {"Noida Sector 62 Tower", "Gurugram Sector 49"}
    phones = {u["phone"] for u in sharma.get("/api/org/users").json()}
    assert GREENLINE_OWNER not in phones and len(phones) == 3


def test_settings_change_stays_in_own_org(login: Login) -> None:
    from app.domain.settings import DEFAULT_ORG_SETTINGS

    w = {"price": 30, "delivery": 30, "payment": 20, "reliability": 10, "quality": 10}
    login(SHARMA_OWNER).put("/api/org/settings", json={**DEFAULT_ORG_SETTINGS, "weights": w})
    greenline = login(GREENLINE_OWNER).get("/api/org").json()
    assert greenline["settings"]["weights"] != w


def test_vendor_sees_only_own_messages(login: Login, seeded: Session) -> None:
    vendors = {v.phone: v for v in seeded.scalars(select(Vendor))}
    now = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    mine = Message(vendor_id=vendors[VENDOR_BALAJI].id, direction="out",
                   external_message_id="t-1", body="for balaji", sent_at=now)  # fmt: skip
    theirs = Message(vendor_id=vendors[VENDOR_GUPTA].id, direction="out",
                     external_message_id="t-2", body="for gupta", sent_at=now)  # fmt: skip
    seeded.add_all([mine, theirs])
    seeded.commit()
    c = login(VENDOR_BALAJI)
    assert [m["body"] for m in c.get("/api/vendor/messages").json()] == ["for balaji"]
    assert c.get(f"/api/vendor/messages/{theirs.id}").status_code == 404
    assert c.get(f"/api/vendor/messages/{mine.id}").status_code == 200
