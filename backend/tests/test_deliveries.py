"""Delivery, receipt, invoice check, closure, ratings, price history (Stage 11)."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Message, PriceHistory, Vendor, WorkOrder
from tests.conftest import DbDemoClock
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, SHARMA_SE
from tests.test_approvals import BALAJI, GUPTA, THREE, approve, to_approval
from tests.test_outreach import tick

Login = Callable[[str], TestClient]
PHOTO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def owner(login: Login) -> TestClient:
    return login(SHARMA_OWNER)


def confirmed_po(owner: TestClient, login: Login, db: Session, biz: DbDemoClock) -> dict[str, Any]:
    s = to_approval(owner, login, db, biz, THREE)
    wo = approve(owner, db, s["rfq"]).json()["work_orders"][0]
    login(BALAJI).post(
        "/api/vendor/messages",
        json={"client_message_id": uuid.uuid4().hex, "button": "Confirm", "rfq_id": s["rfq"]},
    )
    return {**s, "wo": wo}


def dispatch(
    login: Login, wo_id: str, qty: str | None = None, ref: str | None = None, phone: str = BALAJI
) -> Any:
    return login(phone).post(
        f"/api/vendor/work-orders/{wo_id}/dispatch",
        json={"client_ref": ref or uuid.uuid4().hex, "vehicle_no": "up16at1234", "qty": qty},
    )


def receive(c: TestClient, wo_id: str, qty: str, photo: bytes = PHOTO) -> Any:
    return c.post(f"/api/work-orders/{wo_id}/receive?qty={qty}", content=photo)


def invoice(
    c: TestClient,
    wo: dict[str, Any],
    amount: str,
    unit_price: str = "385",
    po_code: str | None = None,
) -> Any:
    q = (f"/api/work-orders/{wo['id']}/invoice?invoice_no=INV-77&po_code={po_code or wo['code']}"
         f"&amount={amount}&unit_price={unit_price}&client_ref={uuid.uuid4().hex}")  # fmt: skip
    return c.post(q, content=b"%PDF-1.4 invoice")


def test_full_lifecycle_to_closure(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    wo = s["wo"]
    assert dispatch(login, wo["id"]).json()["status"] == "in_delivery"
    r = receive(login(SHARMA_SE), wo["id"], "30")
    assert r.status_code == 200 and r.json()["work_order"]["status"] == "delivered"
    inv = invoice(owner, wo, "13629.00").json()
    assert inv["status"] == "matched"
    closed = owner.post(f"/api/work-orders/{wo['id']}/close", json={}).json()
    assert closed["status"] == "closed"
    assert owner.get(f"/api/rfqs/{s['rfq']}").json()["status"] == "closed"
    assert owner.get(f"/api/boms/{s['bom']}").json()["status"] == "closed"
    ph = seeded.scalars(
        select(PriceHistory).where(PriceHistory.work_order_id == uuid.UUID(wo["id"]))
    ).one()
    assert ph.unit_price_paise == 38500 and ph.landed_paise == 45430
    v = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    seeded.refresh(v)
    assert (v.orders_completed, v.on_time_bp, v.qty_accuracy_bp, v.invoice_match_bp) == (
        1,
        10_000,
        10_000,
        10_000,
    )
    assert any(
        "received 30 bag" in m.body
        for m in seeded.scalars(select(Message).where(Message.template_name == "delivery_update"))
    )


def test_receipt_before_dispatch_blocked(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    r = receive(owner, s["wo"]["id"], "30")
    assert r.status_code == 409 and "Nothing has been dispatched" in r.json()["detail"]


def test_short_delivery_flagged_and_closed_with_note(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    wo = s["wo"]
    dispatch(login, wo["id"])
    r = receive(owner, wo["id"], "28").json()
    assert "short_delivery" in r["flags"] and r["work_order"]["status"] == "in_delivery"
    assert (
        owner.post(f"/api/work-orders/{wo['id']}/close", json={}).status_code == 409
    )  # needs a note
    assert invoice(owner, wo, "12720.40").json()["status"] == "matched"  # 28 of 30 bags, pro-rata
    out = owner.post(
        f"/api/work-orders/{wo['id']}/close",
        json={"shortfall_note": "2 bags damaged, not replaced"},
    ).json()
    assert out["status"] == "closed" and out["shortfall_note"].startswith("2 bags")
    v = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    seeded.refresh(v)
    assert v.qty_accuracy_bp == 0


def test_over_delivery_accepts_only_ordered_quantity(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    dispatch(login, s["wo"]["id"])
    r = receive(owner, s["wo"]["id"], "32").json()
    assert r["received_milli"] == 30_000 and "over_delivery" in r["flags"]
    assert r["work_order"]["status"] == "delivered"


def test_partial_dispatch_needs_partial_allowed(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    wo = s["wo"]
    r = dispatch(login, wo["id"], qty="10")
    assert r.status_code == 409 and "does not allow partial" in r.json()["detail"]
    seeded.execute(
        update(WorkOrder).where(WorkOrder.id == uuid.UUID(wo["id"])).values(partial_allowed=True)
    )
    seeded.commit()
    assert dispatch(login, wo["id"], qty="10").status_code == 200
    receive(owner, wo["id"], "10")
    assert dispatch(login, wo["id"]).status_code == 200  # the remaining 20
    out = receive(owner, wo["id"], "20").json()
    assert out["work_order"]["status"] == "delivered"
    assert dispatch(login, wo["id"]).status_code == 409  # nothing left


def test_invoice_price_mismatch_needs_a_person(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    wo = s["wo"]
    dispatch(login, wo["id"])
    receive(owner, wo["id"], "30")
    inv = invoice(owner, wo, "14160.00", unit_price="400").json()
    assert inv["status"] == "flagged" and {"unit_price", "amount"} <= set(inv["mismatch_flags"])
    assert (
        owner.post(f"/api/work-orders/{wo['id']}/close", json={}).status_code == 409
    )  # no auto-accept
    assert (
        login(SHARMA_SE).post(f"/api/invoices/{inv['id']}/accept", json={"note": "ok"}).status_code
        == 403
    )
    owner.post(
        f"/api/invoices/{inv['id']}/accept", json={"note": "Credit note agreed for the difference"}
    )
    assert owner.post(f"/api/work-orders/{wo['id']}/close", json={}).json()["status"] == "closed"
    v = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    seeded.refresh(v)
    assert v.invoice_match_bp == 0


def test_invoice_for_a_different_po(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    inv = invoice(owner, s["wo"], "13629.00", po_code="PO-2026-09999").json()
    assert inv["status"] == "flagged" and "different_po" in inv["mismatch_flags"]


def test_late_delivery_lowers_on_time_rating(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    wo = s["wo"]
    dispatch(login, wo["id"])
    tick(biz, timedelta(days=12))  # needed by 5 Oct; arrives ~7 Oct
    r = receive(owner, wo["id"], "30").json()
    assert r["flags"]["late_days"] >= 1
    invoice(owner, wo, "13629.00")
    owner.post(f"/api/work-orders/{wo['id']}/close", json={})
    v = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    seeded.refresh(v)
    assert v.on_time_bp == 0


def test_dispatch_is_idempotent(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    ref = uuid.uuid4().hex
    a, b = (
        dispatch(login, s["wo"]["id"], ref=ref).json(),
        dispatch(login, s["wo"]["id"], ref=ref).json(),
    )
    assert a["delivery"] == b["delivery"]


def test_unconfirmed_po_cannot_be_dispatched(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    wo = approve(owner, seeded, s["rfq"]).json()["work_orders"][0]
    assert dispatch(login, wo["id"]).status_code == 409


def test_other_vendor_cannot_dispatch(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    assert dispatch(login, s["wo"]["id"], phone=GUPTA).status_code == 404


def test_photo_is_required_and_must_be_an_image(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    dispatch(login, s["wo"]["id"])
    assert receive(owner, s["wo"]["id"], "30", photo=b"").status_code == 422
    assert receive(owner, s["wo"]["id"], "30", photo=b"%PDF-1.4").status_code == 422
    assert receive(owner, s["wo"]["id"], "-1").status_code == 422


def test_site_engineer_receives_but_cannot_close(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    dispatch(login, s["wo"]["id"])
    se = login(SHARMA_SE)
    assert receive(se, s["wo"]["id"], "30").status_code == 200
    assert se.post(f"/api/work-orders/{s['wo']['id']}/close", json={}).status_code == 403


def test_cross_tenant(owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock) -> None:
    s = confirmed_po(owner, login, seeded, biz)
    other = login(GREENLINE_OWNER)
    assert receive(other, s["wo"]["id"], "30").status_code == 404
    assert other.post(f"/api/work-orders/{s['wo']['id']}/close", json={}).status_code == 404
    assert invoice(other, s["wo"], "1").status_code == 404
