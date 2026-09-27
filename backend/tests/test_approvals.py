"""Approval, work orders, capacity conflicts, runner-up, cancellation (Stage 10)."""

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import (
    CapacityReservation,
    Message,
    NegotiationThread,
    Quote,
    Rfq,
    Site,
    User,
    Vendor,
    WorkOrder,
)
from tests.conftest import DbDemoClock
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, SHARMA_PM, SHARMA_SE
from tests.test_outreach import tick

Login = Callable[[str], TestClient]
BALAJI, GUPTA, DELHI, YADAV, MAHALAXMI = (
    "+919000020001",
    "+919000020002",
    "+919000020004",
    "+919000020006",
    "+919000020005",
)
DEBOUNCE = timedelta(seconds=46)


def form(rfq_id: str, price: str, **over: Any) -> dict[str, Any]:
    base = {"client_message_id": uuid.uuid4().hex, "rfq_id": rfq_id, "unit_price": price, "price_unit": "bag",
            "gst_included": False, "gst_percent": "18", "delivery_date": "2026-10-01", "validity_until": "2026-10-20",
            "payment_terms_days": 15}  # fmt: skip
    return {**base, **over}


def to_approval(c: TestClient, login: Login, db: Session, biz: DbDemoClock, quotes: list[tuple[str, str, dict[str, Any]]],
                qty: str = "30", site: str = "Noida Sector 62 Tower", lines: int = 1) -> dict[str, Any]:  # fmt: skip
    """Publish, invite, collect quotes, close, let every vendor refuse to move -> awaiting approval."""
    site_id = db.scalars(select(Site.id).where(Site.name == site)).one()
    rows = [
        {"item": "OPC 53 Grade Cement", "quantity": qty, "unit": "bag", "needed_by": "2026-10-05"}
    ]
    if lines == 2:
        rows.append(
            {"item": "PPC Cement", "quantity": "20", "unit": "bag", "needed_by": "2026-10-05"}
        )
    bom = c.post(
        "/api/boms", json={"site_id": str(site_id), "client_ref": uuid.uuid4().hex, "rows": rows}
    ).json()
    pub = c.post(f"/api/boms/{bom['id']}/publish").json()
    rfq = pub["lines"][0]["rfq"]["id"]
    c.post(f"/api/rfqs/{rfq}/send")
    tick(biz)
    for phone, price, extra in quotes:
        assert (
            login(phone).post("/api/vendor/quotes", json=form(rfq, price, **extra)).json()["status"]
            == "confirmed"
        )
    tick(biz, timedelta(hours=24, minutes=1))
    for phone, _, _ in quotes:
        login(phone).post(
            "/api/vendor/messages",
            json={
                "client_message_id": uuid.uuid4().hex,
                "text": "No discount possible",
                "rfq_id": rfq,
            },
        )
    tick(biz, DEBOUNCE)
    view = c.get(f"/api/rfqs/{rfq}").json()
    assert view["status"] == "awaiting_approval", view["status"]
    return {"rfq": rfq, "bom": bom["id"], "view": view}


def version(db: Session, rfq: str) -> int:
    db.expire_all()
    r = db.get(Rfq, uuid.UUID(rfq))
    assert r is not None
    return r.version


def approve(
    c: TestClient, db: Session, rfq: str, choice: str = "l1", key: str | None = None, **extra: Any
) -> Any:
    body = {
        "idempotency_key": key or uuid.uuid4().hex,
        "choice": choice,
        "version": version(db, rfq),
        **extra,
    }
    return c.post(f"/api/rfqs/{rfq}/approve", json=body)


THREE: list[tuple[str, str, dict[str, Any]]] = [
    (BALAJI, "385", {}),
    (GUPTA, "390", {}),
    (DELHI, "400", {}),
]


@pytest.fixture
def owner(login: Login) -> TestClient:
    return login(SHARMA_OWNER)


def templates_for(db: Session, phone: str) -> list[str]:
    db.expire_all()
    v = db.scalars(select(Vendor).where(Vendor.phone == phone)).one()
    return [
        m.template_name or ""
        for m in db.scalars(
            select(Message)
            .where(Message.vendor_id == v.id, Message.direction == "out")
            .order_by(Message.sent_at)
        )
    ]


# --- approve -------------------------------------------------------------------------------


def test_approve_issues_po_to_winner_and_tells_the_others(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    r = approve(owner, seeded, s["rfq"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "awarded" and len(out["work_orders"]) == 1
    wo = out["work_orders"][0]
    assert wo["vendor"] == "Shree Balaji Cement Traders" and wo["status"] == "issued"
    assert (
        wo["subtotal_paise"] == 1_155_000
        and wo["gst_paise"] == 207_900
        and wo["total_paise"] == 1_362_900
    )
    po_msg = next(
        m for m in seeded.scalars(select(Message).where(Message.template_name == "po_issued"))
    )
    assert (
        "Plot C-14, Sector 62, Noida" in po_msg.body
    )  # exact address only now, only to the winner
    assert "po_issued" not in templates_for(seeded, GUPTA) and "not_selected" in templates_for(
        seeded, GUPTA
    )
    assert owner.get(f"/api/work-orders/{wo['id']}/pdf").content[:4] == b"%PDF"
    assert seeded.scalars(select(CapacityReservation)).one().qty_reserved_milli == 30_000
    assert owner.get(f"/api/boms/{s['bom']}").json()["status"] == "awarded"


def test_double_click_is_idempotent(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    key = uuid.uuid4().hex
    body = {"idempotency_key": key, "choice": "l1", "version": version(seeded, s["rfq"])}
    a = owner.post(f"/api/rfqs/{s['rfq']}/approve", json=body)
    b = owner.post(f"/api/rfqs/{s['rfq']}/approve", json=body)
    assert a.status_code == b.status_code == 200
    assert a.json()["work_orders"] == b.json()["work_orders"]
    assert len(seeded.scalars(select(WorkOrder)).all()) == 1


def test_second_approver_sees_who_approved(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    v = version(seeded, s["rfq"])
    pm = login(SHARMA_PM)
    assert (
        owner.post(
            f"/api/rfqs/{s['rfq']}/approve",
            json={"idempotency_key": uuid.uuid4().hex, "choice": "l1", "version": v},
        ).status_code
        == 200
    )
    r = pm.post(
        f"/api/rfqs/{s['rfq']}/approve",
        json={"idempotency_key": uuid.uuid4().hex, "choice": "l1", "version": v},
    )
    assert r.status_code == 409 and r.json()["detail"].startswith(
        "Already approved by Rakesh Sharma at "
    )


def test_two_users_approve_at_the_same_time(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    v = version(seeded, s["rfq"])
    pm = login(SHARMA_PM)

    def go(c: TestClient) -> Any:
        return c.post(
            f"/api/rfqs/{s['rfq']}/approve",
            json={"idempotency_key": uuid.uuid4().hex, "choice": "l1", "version": v},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(go, [owner, pm]))
    assert codes == [200, 409]
    assert len(seeded.scalars(select(WorkOrder)).all()) == 1


def test_pm_above_limit_is_routed_to_owner(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    seeded.execute(
        update(User).where(User.phone == SHARMA_PM).values(approval_limit_paise=1_000_000)
    )  # ₹10,000
    seeded.commit()
    r = approve(login(SHARMA_PM), seeded, s["rfq"])
    assert r.status_code == 200 and r.json()["decision"] == "routed_to_owner"
    assert "Sent to the owner" in r.json()["message"] and r.json()["status"] == "awaiting_approval"
    assert owner.get("/api/dashboard/actions").json()["approvals"][0]["routed_to_owner"] is True
    assert approve(owner, seeded, s["rfq"]).json()["status"] == "awarded"


def test_pm_within_limit_approves(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    assert approve(login(SHARMA_PM), seeded, s["rfq"]).json()["status"] == "awarded"


def test_site_engineer_cannot_approve(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    assert approve(login(SHARMA_SE), seeded, s["rfq"]).status_code == 403


def test_expired_offer_blocks_approval_and_can_be_reconfirmed(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    tick(biz, timedelta(days=30))  # every quote's validity (20 Oct) has passed
    r = approve(owner, seeded, s["rfq"])
    assert r.status_code == 409 and "Ask them to reconfirm" in r.json()["detail"]
    balaji = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    out = owner.post(f"/api/rfqs/{s['rfq']}/reconfirm", json={"vendor_id": str(balaji.id)}).json()
    assert out["status"] == "bidding"
    assert any(
        "passed its validity" in m.body
        for m in seeded.scalars(select(Message).where(Message.vendor_id == balaji.id))
    )


def test_split_award_issues_several_pos(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    quotes: list[tuple[str, str, dict[str, Any]]] = [
        (BALAJI, "384", {}),
        (GUPTA, "382", {}),
        (YADAV, "390", {}),
        (MAHALAXMI, "379", {}),
    ]
    s = to_approval(owner, login, seeded, biz, quotes, qty="2000")
    r = approve(owner, seeded, s["rfq"], choice="split")
    assert r.status_code == 200, r.text
    wos = r.json()["work_orders"]
    assert len(wos) >= 2 and sum(w["qty_milli"] for w in wos) == 2_000_000


# --- capacity conflict, decline, expiry, runner-up ------------------------------------------


def test_capacity_conflict_across_builders(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    """Gupta can do 1,000 bags a week. Sharma and Greenline both award Gupta 600 bags for the same week."""
    cheap_gupta: list[tuple[str, str, dict[str, Any]]] = [
        (GUPTA, "370", {}),
        (BALAJI, "395", {}),
        (YADAV, "398", {}),
    ]
    a = to_approval(owner, login, seeded, biz, cheap_gupta, qty="600")
    greenline = login(GREENLINE_OWNER)
    b = to_approval(
        greenline,
        login,
        seeded,
        biz,
        [(GUPTA, "370", {}), (BALAJI, "395", {})],
        qty="600",
        site="Dwarka Sector 19",
    )
    assert approve(owner, seeded, a["rfq"]).status_code == 200
    r = approve(greenline, seeded, b["rfq"])
    assert (
        r.status_code == 409
        and "no longer has capacity" in r.json()["detail"]
        and "runner-up" in r.json()["detail"]
    )
    balaji = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    ok = approve(greenline, seeded, b["rfq"], choice=str(balaji.id))  # one tap on the runner-up
    assert (
        ok.status_code == 200
        and ok.json()["work_orders"][0]["vendor"] == "Shree Balaji Cement Traders"
    )


def test_vendor_confirms_po(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    approve(owner, seeded, s["rfq"])
    po_msg = seeded.scalars(select(Message).where(Message.template_name == "po_issued")).one()
    login(BALAJI).post(
        "/api/vendor/messages",
        json={
            "client_message_id": uuid.uuid4().hex,
            "button": "Confirm",
            "rfq_id": s["rfq"],
            "reply_to": str(po_msg.id),
        },
    )
    seeded.expire_all()
    wo = seeded.scalars(select(WorkOrder)).one()
    assert wo.status == "vendor_confirmed" and wo.confirmed_at is not None


def test_vendor_declines_runner_up_offered(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    approve(owner, seeded, s["rfq"])
    login(BALAJI).post(
        "/api/vendor/messages",
        json={"client_message_id": uuid.uuid4().hex, "button": "Decline", "rfq_id": s["rfq"]},
    )
    seeded.expire_all()
    wo = seeded.scalars(select(WorkOrder)).one()
    assert wo.status == "vendor_declined"
    assert seeded.scalars(select(CapacityReservation)).one().released_at is not None
    rfq = owner.get(f"/api/rfqs/{s['rfq']}").json()
    assert rfq["status"] == "awaiting_approval"
    comp = owner.get(f"/api/rfqs/{s['rfq']}/comparison").json()
    actions = owner.get("/api/dashboard/actions").json()
    assert actions["approvals"][0]["runner_up"] is True
    gupta = seeded.scalars(select(Vendor).where(Vendor.phone == GUPTA)).one()
    assert comp["status"] == "awaiting_approval"
    r = approve(owner, seeded, s["rfq"], choice=str(gupta.id))
    assert (
        r.status_code == 200 and r.json()["work_orders"][-1]["vendor"] == "Gupta Building Materials"
    )


def test_unconfirmed_po_expires_and_runner_up_offered(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    approve(owner, seeded, s["rfq"])
    tick(biz, timedelta(days=1))  # 4 working hours pass
    seeded.expire_all()
    assert seeded.scalars(select(WorkOrder)).one().status == "expired"
    r = seeded.get(Rfq, uuid.UUID(s["rfq"]))
    assert r is not None and r.status == "awaiting_approval" and r.match_report["runner_up"]


def test_runner_up_also_expired_then_rebid(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    quotes = [(BALAJI, "385", {}), (GUPTA, "390", {"validity_until": "2026-09-29"})]
    s = to_approval(owner, login, seeded, biz, quotes)
    approve(owner, seeded, s["rfq"])
    tick(
        biz, timedelta(days=5)
    )  # PO expired on day 1 (Gupta offered as runner-up); Gupta's quote ran out on 29 Sep
    seeded.expire_all()
    r = seeded.get(Rfq, uuid.UUID(s["rfq"]))
    assert r is not None and r.match_report["runner_up"]
    blocked = approve(owner, seeded, s["rfq"], choice=r.match_report["runner_up"])
    assert blocked.status_code == 409 and "expired" in blocked.json()["detail"]
    out = owner.post(f"/api/rfqs/{s['rfq']}/rebid").json()
    assert out["status"] == "bidding"
    tick(biz)
    balaji = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    assert (
        seeded.scalars(
            select(Quote).where(Quote.vendor_id == balaji.id, Quote.status == "confirmed")
        ).first()
        is None
    )  # failed vendor out
    assert templates_for(seeded, GUPTA).count("rfq_invite") == 2  # re-invited


# --- cancellation ------------------------------------------------------------------------


def test_cancel_bom_during_negotiation(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    site_id = seeded.scalars(select(Site.id).where(Site.name == "Noida Sector 62 Tower")).one()
    bom = owner.post("/api/boms", json={"site_id": str(site_id), "client_ref": uuid.uuid4().hex,
                                         "rows": [{"item": "OPC 53 Grade Cement", "quantity": "30", "unit": "bag", "needed_by": "2026-10-05"}]}).json()  # fmt: skip
    rfq = owner.post(f"/api/boms/{bom['id']}/publish").json()["lines"][0]["rfq"]["id"]
    owner.post(f"/api/rfqs/{rfq}/send")
    tick(biz)
    for phone, price, _ in THREE:
        login(phone).post("/api/vendor/quotes", json=form(rfq, price))
    tick(biz, timedelta(hours=24, minutes=1))
    assert owner.get(f"/api/rfqs/{rfq}").json()["status"] == "negotiating"
    out = owner.post(f"/api/boms/{bom['id']}/cancel").json()
    assert out["status"] == "cancelled"
    assert {t.state for t in seeded.scalars(select(NegotiationThread))} == {"closed"}
    assert "rfq_cancelled" in templates_for(seeded, GUPTA)
    assert seeded.scalars(select(WorkOrder)).first() is None
    tick(biz, timedelta(hours=8))  # no nudges after cancellation
    assert templates_for(seeded, GUPTA)[-1] == "rfq_cancelled"


def test_cancel_after_po_issued_releases_capacity(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(
        owner, login, seeded, biz, THREE, lines=2
    )  # line 2 stays open: BOM partially awarded
    approve(owner, seeded, s["rfq"])
    assert owner.get(f"/api/boms/{s['bom']}").json()["status"] == "partially_awarded"
    owner.post(f"/api/boms/{s['bom']}/cancel")
    seeded.expire_all()
    wo = seeded.scalars(select(WorkOrder)).one()
    assert wo.status == "cancelled"
    assert seeded.scalars(select(CapacityReservation)).one().released_at is not None
    assert any(
        "cancelled by the buyer" in m.body
        for m in seeded.scalars(select(Message).where(Message.template_name == "delivery_update"))
    )


def test_cancel_single_po(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    wo = approve(owner, seeded, s["rfq"]).json()["work_orders"][0]
    assert owner.post(f"/api/work-orders/{wo['id']}/cancel").json()["status"] == "cancelled"
    assert owner.post(f"/api/work-orders/{wo['id']}/cancel").status_code == 409
    assert (
        owner.post(f"/api/boms/{s['bom']}/cancel").status_code == 409
    )  # awarded BOM: cancel POs instead


# --- other actions --------------------------------------------------------------------------


def test_compare_again_and_review(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    assert owner.post(f"/api/rfqs/{s['rfq']}/compare-again").json()["recommendation"][
        "l1_vendor_id"
    ]
    assert login(SHARMA_SE).post(
        f"/api/rfqs/{s['rfq']}/review", json={"note": "please check freight"}
    ).json() == {"ok": True}


def test_above_max_needs_override(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    owner.put(
        f"/api/rfqs/{s['rfq']}/limits", json={"max_price_paise": 45000}
    )  # everyone lands above ₹450
    assert approve(owner, seeded, s["rfq"]).status_code == 409
    balaji = seeded.scalars(select(Vendor).where(Vendor.phone == BALAJI)).one()
    r = approve(owner, seeded, s["rfq"], choice=str(balaji.id), override_above_max=True)
    assert r.status_code == 200


def test_cross_tenant(owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock) -> None:
    s = to_approval(owner, login, seeded, biz, THREE)
    wo = approve(owner, seeded, s["rfq"]).json()["work_orders"][0]
    other = login(GREENLINE_OWNER)
    assert other.get(f"/api/work-orders/{wo['id']}").status_code == 404
    assert other.get(f"/api/work-orders/{wo['id']}/pdf").status_code == 404
    assert other.post(f"/api/work-orders/{wo['id']}/cancel").status_code == 404
    assert other.post(f"/api/rfqs/{s['rfq']}/compare-again").status_code == 404
    assert other.get("/api/work-orders").json() == []
