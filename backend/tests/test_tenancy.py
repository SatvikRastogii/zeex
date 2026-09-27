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

from app.db.models import Bom, BomLine, BuilderOrg, Message, Site, User, Vendor
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, VENDOR_BALAJI, VENDOR_GUPTA

Login = Callable[[str], TestClient]


def greenline_ids(db: Session) -> dict[str, uuid.UUID]:
    org = db.scalars(select(BuilderOrg).where(BuilderOrg.name == "Greenline Infra")).one()
    site = db.scalars(select(Site).where(Site.builder_org_id == org.id)).first()
    assert site is not None
    bom = Bom(builder_org_id=org.id, public_code="BOM-GL-1", site_id=site.id, status="validated",
              original_file_ref=f"boms/{org.id}/x.csv")  # fmt: skip
    db.add(bom)
    db.flush()
    line = BomLine(builder_org_id=org.id, bom_id=bom.id, line_no=1, raw_text="sand",
                   qty_canonical_milli=1000, unit="cft", qty_entered="1",
                   needed_by=datetime(2026, 10, 5).date())  # fmt: skip
    db.add(line)
    db.commit()
    return {
        "site": site.id,
        "user": db.scalars(select(User).where(User.phone == GREENLINE_OWNER)).one().id,
        "bom": bom.id,
        "line": line.id,
    }


# (method, path template, json body, kind of id)
TENANT_ENDPOINTS = [
    ("GET", "/api/sites/{id}", None, "site"),
    ("PATCH", "/api/org/users/{id}", {"is_active": False}, "user"),
    ("GET", "/api/boms/{id}", None, "bom"),
    ("GET", "/api/boms/{id}/file", None, "bom"),
    ("POST", "/api/boms/{id}/publish", None, "bom"),
    ("POST", "/api/boms/{id}/cancel", None, "bom"),
    ("POST", "/api/boms/validate", {"site_id": "{id}", "rows": []}, "site"),
]


@pytest.mark.parametrize(("method", "path", "body", "kind"), TENANT_ENDPOINTS)
def test_other_tenants_rows_are_404(
    login: Login, seeded: Session, method: str, path: str, body: object, kind: str
) -> None:
    target = greenline_ids(seeded)[kind]
    r = login(SHARMA_OWNER).request(method, path.format(id=target), json=_fill(body, target))
    assert r.status_code == 404


@pytest.mark.parametrize(("method", "path", "body", "kind"), TENANT_ENDPOINTS)
def test_missing_rows_are_also_404(
    login: Login, method: str, path: str, body: object, kind: str
) -> None:
    missing = uuid.uuid4()
    r = login(SHARMA_OWNER).request(method, path.format(id=missing), json=_fill(body, missing))
    assert r.status_code == 404


def _fill(body: object, target: uuid.UUID) -> object:
    if isinstance(body, dict):
        return {k: str(target) if v == "{id}" else v for k, v in body.items()}
    return body


def test_other_tenants_bom_line_is_404(login: Login, seeded: Session) -> None:
    ids = greenline_ids(seeded)
    r = login(SHARMA_OWNER).patch(
        f"/api/boms/{ids['bom']}/lines/{ids['line']}", json={"quantity": "2"}
    )
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
