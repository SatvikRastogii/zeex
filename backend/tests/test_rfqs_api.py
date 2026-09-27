"""Matching through the API on the seeded Delhi NCR data."""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import (
    BuilderOrg,
    BuilderVendorLink,
    CapacityReservation,
    CatalogItem,
    Rfq,
    Site,
    Vendor,
)
from app.jobs.clock import ist_week_start
from tests.factories import work_order_for
from tests.phones import ARORA_OWNER, GREENLINE_OWNER, SHARMA_OWNER, SHARMA_PM, SHARMA_SE

Login = Callable[[str], TestClient]
NEEDED = "2026-10-05"


def publish_line(
    c: TestClient, db: Session, site: str, item: str, qty: str, unit: str
) -> dict[str, Any]:
    site_id = db.scalars(select(Site.id).where(Site.name == site)).one()
    bom = c.post("/api/boms", json={"site_id": str(site_id), "client_ref": uuid.uuid4().hex,
                                    "rows": [{"item": item, "quantity": qty, "unit": unit, "needed_by": NEEDED}]})  # fmt: skip
    assert bom.status_code == 201, bom.text
    pub = c.post(f"/api/boms/{bom.json()['id']}/publish").json()
    rfq: dict[str, Any] = c.get(f"/api/rfqs/{pub['lines'][0]['rfq']['id']}").json()
    return rfq


def names(rfq: dict[str, Any], status: str = "proposed") -> list[str]:
    return [s["vendor"] for s in rfq["shortlist"] if s["status"] == status]


def excluded(rfq: dict[str, Any]) -> dict[str, str]:
    return {e["vendor"]: e["reason"] for e in rfq["match_report"]["excluded"]}


def test_happy_path_five_cement_vendors_for_noida(login: Login, seeded: Session) -> None:
    rfq = publish_line(
        login(SHARMA_OWNER), seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag"
    )
    assert rfq["status"] == "matching"
    assert len(names(rfq)) == 5
    assert "Singh Cement Agency" in excluded(rfq)  # 38 km vs 35 km radius
    top = rfq["shortlist"][0]
    assert top["score"] >= rfq["shortlist"][-1]["score"] and " km" in top["reason"]


def test_blocked_by_this_builder_only(login: Login, seeded: Session) -> None:
    greenline = publish_line(
        login(GREENLINE_OWNER), seeded, "Dwarka Sector 19", "OPC 53 Grade Cement", "30", "bag"
    )
    assert excluded(greenline)["Delhi Cement Depot"] == "Blocked by your organisation"
    sharma = publish_line(
        login(SHARMA_OWNER), seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag"
    )
    assert "Delhi Cement Depot" in names(sharma)


def test_vendor_linked_to_a_but_not_b(login: Login, seeded: Session) -> None:
    arora = publish_line(
        login(ARORA_OWNER), seeded, "Faridabad Sector 21", "OPC 53 Grade Cement", "30", "bag"
    )
    assert "Gupta Building Materials" not in names(arora)
    assert "Gupta Building Materials" not in excluded(arora)  # not even visible
    sharma = publish_line(
        login(SHARMA_OWNER), seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag"
    )
    assert "Gupta Building Materials" in names(sharma)


def test_opted_out_vendor_filtered_and_single_match_warned(login: Login, seeded: Session) -> None:
    rfq = publish_line(
        login(ARORA_OWNER), seeded, "Faridabad Sector 21", "red bricks", "5000", "nos"
    )
    assert excluded(rfq)["Rana Bricks Supply"] == "Opted out of messages"
    assert names(rfq) == ["Chaudhary Brick Kiln"]
    assert "no competition" in rfq["match_report"]["warning"]


def test_missing_gstin_filtered(login: Login, seeded: Session) -> None:
    rfq = publish_line(
        login(GREENLINE_OWNER), seeded, "Ghaziabad Raj Nagar Extension", "river sand", "500", "cft"
    )
    assert excluded(rfq)["Tyagi Sand and Grit"] == "No valid GSTIN on record"


def test_all_blocked_gives_no_vendors_matched(login: Login, seeded: Session) -> None:
    org = seeded.scalars(
        select(BuilderOrg.id).where(BuilderOrg.name == "Sharma Constructions")
    ).one()
    seeded.execute(
        update(BuilderVendorLink)
        .where(BuilderVendorLink.builder_org_id == org)
        .values(status="blocked")
    )
    seeded.commit()
    rfq = publish_line(
        login(SHARMA_OWNER), seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag"
    )
    assert rfq["status"] == "no_vendors_matched"
    assert (
        rfq["shortlist"] == [] and "Widen the search radius" in rfq["match_report"]["suggestions"]
    )


def test_vendor_at_capacity_for_the_week(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    balaji = seeded.scalars(
        select(Vendor).where(Vendor.display_name == "Shree Balaji Cement Traders")
    ).one()
    item = seeded.scalars(select(CatalogItem).where(CatalogItem.code == "OPC53")).one()
    wo = work_order_for(seeded, balaji.id)
    seeded.add(CapacityReservation(vendor_id=balaji.id, catalog_item_id=item.id,
                                   week_start=ist_week_start(date.fromisoformat(NEEDED)),
                                   qty_reserved_milli=balaji.capacity_per_week["OPC53"], work_order_id=wo.id))  # fmt: skip
    seeded.commit()
    rfq = publish_line(c, seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag")
    assert excluded(rfq)["Shree Balaji Cement Traders"] == "No capacity left in the needed-by week"


def test_same_inputs_same_order(login: Login, seeded: Session) -> None:
    """Exact score ties are covered in test_matching.py; here the ordering must repeat."""
    first = publish_line(login(SHARMA_OWNER), seeded, "Gurugram Sector 49", "saria", "2", "tonne")
    again = publish_line(login(SHARMA_OWNER), seeded, "Gurugram Sector 49", "saria", "2", "tonne")
    assert [s["vendor"] for s in again["shortlist"]] == [s["vendor"] for s in first["shortlist"]]


def test_widen_radius_rematch(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    rfq = publish_line(c, seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag")
    wider = c.post(f"/api/rfqs/{rfq['id']}/match", json={"extra_radius_km": 5}).json()
    assert "Singh Cement Agency" not in excluded(wider)
    assert wider["match_report"]["extra_radius_km"] == 5
    assert len(names(wider)) == 5  # still top 5, Singh now eligible


def test_remove_and_add_vendor(login: Login, seeded: Session) -> None:
    c = login(SHARMA_PM)
    rfq = publish_line(c, seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag")
    first = rfq["shortlist"][0]
    out = c.post(
        f"/api/rfqs/{rfq['id']}/shortlist",
        json={"vendor_id": first["vendor_id"], "action": "remove"},
    ).json()
    assert first["vendor"] not in names(out) and first["vendor"] in names(out, "removed")
    back = c.post(
        f"/api/rfqs/{rfq['id']}/shortlist", json={"vendor_id": first["vendor_id"], "action": "add"}
    ).json()
    assert first["vendor"] in names(back)


def test_cannot_add_ineligible_vendor(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    rfq = publish_line(c, seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag")
    cands = {x["vendor"]: x for x in c.get(f"/api/rfqs/{rfq['id']}/candidates").json()}
    singh = cands["Singh Cement Agency"]
    assert singh["eligible"] is False
    r = c.post(
        f"/api/rfqs/{rfq['id']}/shortlist", json={"vendor_id": singh["vendor_id"], "action": "add"}
    )
    assert r.status_code == 422 and "Does not serve" in r.json()["detail"]


def test_add_vendor_after_no_match_reopens_matching(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    rfq = publish_line(c, seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag")
    for s in rfq["shortlist"]:
        c.post(
            f"/api/rfqs/{rfq['id']}/shortlist",
            json={"vendor_id": s["vendor_id"], "action": "remove"},
        )
    seeded.execute(
        update(Rfq).where(Rfq.id == uuid.UUID(rfq["id"])).values(status="no_vendors_matched")
    )
    seeded.commit()
    out = c.post(
        f"/api/rfqs/{rfq['id']}/shortlist",
        json={"vendor_id": rfq["shortlist"][0]["vendor_id"], "action": "add"},
    ).json()
    assert out["status"] == "matching"


@pytest.mark.parametrize(
    ("phone", "status"), [(SHARMA_OWNER, 200), (SHARMA_PM, 200), (SHARMA_SE, 403)]
)
def test_shortlist_edit_permission(login: Login, seeded: Session, phone: str, status: int) -> None:
    rfq = publish_line(
        login(SHARMA_OWNER), seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag"
    )
    r = login(phone).post(
        f"/api/rfqs/{rfq['id']}/shortlist",
        json={"vendor_id": rfq["shortlist"][0]["vendor_id"], "action": "remove"},
    )
    assert r.status_code == status


def test_site_engineer_can_view_rfq(login: Login, seeded: Session) -> None:
    rfq = publish_line(
        login(SHARMA_OWNER), seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag"
    )
    assert login(SHARMA_SE).get(f"/api/rfqs/{rfq['id']}").status_code == 200


def test_shortlist_locked_after_invites(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    rfq = publish_line(c, seeded, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag")
    seeded.execute(update(Rfq).where(Rfq.id == uuid.UUID(rfq["id"])).values(status="invited"))
    seeded.commit()
    r = c.post(
        f"/api/rfqs/{rfq['id']}/shortlist",
        json={"vendor_id": rfq["shortlist"][0]["vendor_id"], "action": "remove"},
    )
    assert r.status_code == 409
    assert c.post(f"/api/rfqs/{rfq['id']}/match", json={}).status_code == 409


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/rfqs/{id}", None),
        ("POST", "/api/rfqs/{id}/match", {}),
        ("GET", "/api/rfqs/{id}/candidates", None),
        ("POST", "/api/rfqs/{id}/shortlist", {"vendor_id": str(uuid.uuid4()), "action": "remove"}),
    ],
)
def test_rfq_endpoints_cross_tenant_404(
    login: Login, seeded: Session, method: str, path: str, body: object
) -> None:
    rfq = publish_line(
        login(GREENLINE_OWNER), seeded, "Dwarka Sector 19", "OPC 53 Grade Cement", "30", "bag"
    )
    r = login(SHARMA_OWNER).request(method, path.format(id=rfq["id"]), json=body)
    assert r.status_code == 404
