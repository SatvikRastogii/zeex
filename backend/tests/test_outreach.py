"""Outreach agent + vendor inbox + admin clock, end to end through the API."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job, Message, Rfq, RfqInvitation, Site, Vendor
from app.db.session import get_engine
from app.jobs import handlers  # noqa: F401
from app.jobs.clock import IST
from app.jobs.demo_clock import load_clock
from app.jobs.queue import run_due
from tests.conftest import DbDemoClock
from tests.phones import ADMIN, SHARMA_OWNER, SHARMA_SE, VENDOR_BALAJI, VENDOR_GUPTA

Login = Callable[[str], TestClient]


def tick(biz: DbDemoClock, delta: timedelta = timedelta(0)) -> int:
    if delta:
        biz.advance(delta)
    make = sessionmaker(get_engine(), expire_on_commit=False)
    with make() as s:
        clock = load_clock(s)
    return run_due(make, clock)


def published_rfq(c: TestClient, db: Session, qty: str = "30") -> dict[str, Any]:
    site = db.scalars(select(Site.id).where(Site.name == "Noida Sector 62 Tower")).one()
    bom = c.post("/api/boms", json={"site_id": str(site), "client_ref": uuid.uuid4().hex,
                                    "rows": [{"item": "OPC 53 Grade Cement", "quantity": qty, "unit": "bag", "needed_by": "2026-10-05"}]}).json()  # fmt: skip
    pub = c.post(f"/api/boms/{bom['id']}/publish").json()
    return {"bom": pub, "rfq_id": pub["lines"][0]["rfq"]["id"], "line_id": pub["lines"][0]["id"]}


def messages(db: Session, template: str | None = None) -> list[Message]:
    db.expire_all()
    q = select(Message).where(Message.direction == "out")
    if template:
        q = q.where(Message.template_name == template)
    return list(db.scalars(q.order_by(Message.sent_at)))


@pytest.fixture
def owner(login: Login) -> TestClient:
    return login(SHARMA_OWNER)


def test_send_rfqs_in_working_hours(owner: TestClient, seeded: Session, biz: DbDemoClock) -> None:
    r = published_rfq(owner, seeded)
    rfq = owner.post(f"/api/rfqs/{r['rfq_id']}/send").json()
    assert rfq["status"] == "bidding"
    assert {s["status"] for s in rfq["shortlist"]} == {"queued"}
    tick(biz)
    invites = messages(seeded, "rfq_invite")
    assert len(invites) == 5
    body = invites[0].body
    assert "Sector 62, Noida" in body and "Plot C-14" not in body  # area only, never the address
    assert invites[0].payload["form"]["type"] == "quote_form"
    rfq = owner.get(f"/api/rfqs/{r['rfq_id']}").json()
    assert {s["status"] for s in rfq["shortlist"]} == {"invited"}


def test_send_is_idempotent(owner: TestClient, seeded: Session, biz: DbDemoClock) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    assert owner.post(f"/api/rfqs/{r['rfq_id']}/send").status_code == 200
    tick(biz)
    assert len(messages(seeded, "rfq_invite")) == 5


def test_outside_working_hours_deferred_to_next_morning(
    owner: TestClient, seeded: Session, biz: DbDemoClock
) -> None:
    biz.advance(timedelta(hours=7))  # 21:05 IST
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)
    assert messages(seeded, "rfq_invite") == []
    tick(biz, timedelta(hours=12))  # 09:05 next day
    sent = messages(seeded, "rfq_invite")
    assert len(sent) == 5
    assert sent[0].sent_at is not None and sent[0].sent_at.astimezone(IST).hour == 9


def test_reminder_at_half_window_only_to_non_responders(
    owner: TestClient, seeded: Session, biz: DbDemoClock
) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)
    responder = seeded.scalars(
        select(RfqInvitation).where(RfqInvitation.rfq_id == uuid.UUID(r["rfq_id"]))
    ).first()
    assert responder is not None
    seeded.execute(
        update(RfqInvitation).where(RfqInvitation.id == responder.id).values(status="responded")
    )
    seeded.commit()
    # 50% of 24 h = 02:05 IST -> deferred to 09:00
    tick(biz, timedelta(hours=12))
    assert messages(seeded, "bid_reminder") == []
    tick(biz, timedelta(hours=7))
    reminders = messages(seeded, "bid_reminder")
    assert len(reminders) == 4 and responder.vendor_id not in {m.vendor_id for m in reminders}
    tick(biz, timedelta(minutes=30))  # nothing more: one reminder only
    assert len(messages(seeded, "bid_reminder")) == 4


def test_clock_jump_past_every_deadline_fires_in_order(
    owner: TestClient, seeded: Session, biz: DbDemoClock
) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    # No quotes at all: invite, reminder, close -> window extended once (second reminder),
    # second close -> insufficient quotes, closed notice. All from one jump, in order.
    tick(biz, timedelta(days=3))
    balaji = seeded.scalars(select(Vendor).where(Vendor.phone == VENDOR_BALAJI)).one()
    seq = [m.template_name for m in messages(seeded) if m.vendor_id == balaji.id]
    assert seq == ["rfq_invite", "bid_reminder", "bid_reminder", "bid_closed"]
    seeded.expire_all()
    assert seeded.get(Rfq, uuid.UUID(r["rfq_id"])).status == "insufficient_quotes"  # type: ignore[union-attr]


def quote_form(rfq_id: str, price: str) -> dict[str, Any]:
    return {"client_message_id": uuid.uuid4().hex, "rfq_id": rfq_id, "unit_price": price, "price_unit": "bag",
            "delivery_date": "2026-10-01", "validity_until": "2026-10-20", "payment_terms_days": 15}  # fmt: skip


def test_bid_close_moves_to_evaluating(
    owner: TestClient, seeded: Session, biz: DbDemoClock, login: Login
) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)
    for phone, price in ((VENDOR_BALAJI, "385"), (VENDOR_GUPTA, "380")):
        assert (
            login(phone).post("/api/vendor/quotes", json=quote_form(r["rfq_id"], price)).status_code
            == 200
        )
    tick(biz, timedelta(hours=23, minutes=59))
    assert owner.get(f"/api/rfqs/{r['rfq_id']}").json()["status"] == "bidding"
    tick(biz, timedelta(minutes=2))
    assert owner.get(f"/api/rfqs/{r['rfq_id']}").json()["status"] == "evaluating"
    assert len(messages(seeded, "bid_closed")) == 5


def test_opted_out_on_shortlist_is_skipped(
    owner: TestClient, seeded: Session, biz: DbDemoClock
) -> None:
    r = published_rfq(owner, seeded)
    seeded.execute(
        update(Vendor)
        .where(Vendor.phone == VENDOR_GUPTA)
        .values(opted_out_at=load_clock(seeded).now())
    )
    seeded.commit()
    rfq = owner.post(f"/api/rfqs/{r['rfq_id']}/send").json()
    gupta = next(s for s in rfq["shortlist"] if s["vendor"] == "Gupta Building Materials")
    assert gupta["status"] == "skipped_opted_out"
    tick(biz)
    assert len(messages(seeded, "rfq_invite")) == 4


def test_opt_out_after_queueing_blocks_and_logs(
    owner: TestClient, seeded: Session, biz: DbDemoClock, login: Login
) -> None:
    biz.advance(timedelta(hours=7))  # evening: invites wait for morning
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    vendor = login(VENDOR_GUPTA)
    vendor.post("/api/vendor/messages", json={"client_message_id": "stop-1", "text": "STOP"})
    tick(biz, timedelta(hours=12))
    blocked = [m for m in messages(seeded, "rfq_invite") if m.status == "blocked"]
    assert len(blocked) == 1
    rfq = owner.get(f"/api/rfqs/{r['rfq_id']}").json()
    assert (
        next(s for s in rfq["shortlist"] if s["vendor"] == "Gupta Building Materials")["status"]
        == "blocked"
    )


def test_max_15_invites(owner: TestClient, seeded: Session, biz: DbDemoClock) -> None:
    r = published_rfq(owner, seeded)
    rfq_id = uuid.UUID(r["rfq_id"])
    rfq = seeded.get(Rfq, rfq_id)
    assert rfq is not None
    base = seeded.scalars(select(Vendor).where(Vendor.phone == VENDOR_BALAJI)).one()
    for i in range(12):  # 5 matched + 12 clones = 17 proposed
        v = Vendor(legal_name=f"Clone {i}", display_name=f"Clone {i}", phone=f"+91900009{i:04d}",
                   gstin=None, lat=base.lat, lng=base.lng, opted_in_at=base.opted_in_at)  # fmt: skip
        seeded.add(v)
        seeded.flush()
        seeded.add(
            RfqInvitation(
                builder_org_id=rfq.builder_org_id, rfq_id=rfq_id, vendor_id=v.id, status="proposed"
            )
        )
    seeded.commit()
    out = owner.post(f"/api/rfqs/{r['rfq_id']}/send").json()
    statuses = [s["status"] for s in out["shortlist"]]
    assert statuses.count("queued") == 15 and statuses.count("skipped_limit") == 2


def test_stale_rfq_resent_with_the_change(
    owner: TestClient, seeded: Session, biz: DbDemoClock
) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)
    out = owner.patch(
        f"/api/boms/{r['bom']['id']}/lines/{r['line_id']}", json={"quantity": "45"}
    ).json()
    assert out["lines"][0]["rfq"]["stale"] is True
    tick(biz)
    updates = messages(seeded, "rfq_update")
    assert len(updates) == 5 and "45 bag" in updates[0].body
    assert owner.get(f"/api/rfqs/{r['rfq_id']}").json()["stale"] is False


def test_site_engineer_cannot_send(login: Login, owner: TestClient, seeded: Session) -> None:
    r = published_rfq(owner, seeded)
    assert login(SHARMA_SE).post(f"/api/rfqs/{r['rfq_id']}/send").status_code == 403


def test_send_twice_from_wrong_state(owner: TestClient, seeded: Session, biz: DbDemoClock) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz, timedelta(days=3))  # closed twice: insufficient quotes
    assert owner.post(f"/api/rfqs/{r['rfq_id']}/send").status_code == 409


# --- vendor inbox ---------------------------------------------------------------------------


def test_vendor_inbox_conversations_and_reply(
    owner: TestClient, seeded: Session, biz: DbDemoClock, login: Login
) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)
    v = login(VENDOR_BALAJI)
    convs = v.get("/api/vendor/conversations").json()
    assert convs[0]["builder"] == "Sharma Constructions" and convs[0]["rfq_code"].startswith("RFQ-")
    reply = {"client_message_id": "abc-123", "text": "rate 380 per bag", "rfq_id": r["rfq_id"]}
    first = v.post("/api/vendor/messages", json=reply).json()
    again = v.post("/api/vendor/messages", json=reply).json()
    assert first["duplicate"] is False and again["duplicate"] is True
    thread = v.get(f"/api/vendor/conversations/{r['rfq_id']}").json()["messages"]
    assert [m["direction"] for m in thread] == ["out", "in"]


def test_vendor_cannot_reply_on_rfq_they_were_not_invited_to(
    owner: TestClient, seeded: Session, login: Login
) -> None:
    r = published_rfq(owner, seeded)
    singh = login("+919000020003")  # not on this shortlist
    resp = singh.post(
        "/api/vendor/messages",
        json={"client_message_id": "x-12345", "text": "hi", "rfq_id": r["rfq_id"]},
    )
    assert resp.status_code == 404
    assert singh.get(f"/api/vendor/conversations/{r['rfq_id']}").status_code == 404


def test_stop_from_inbox(login: Login, seeded: Session) -> None:
    v = login(VENDOR_BALAJI)
    v.post("/api/vendor/messages", json={"client_message_id": "stop-xyz", "text": "STOP"})
    conv = v.get("/api/vendor/conversations/general").json()
    assert conv["opted_out"] is True and "no longer receive" in conv["messages"][-1]["body"]


# --- admin demo clock ---------------------------------------------------------------------


def test_admin_advance_runs_due_jobs(
    owner: TestClient, seeded: Session, biz: DbDemoClock, login: Login
) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    admin = login(ADMIN)
    pending = admin.get("/api/admin/jobs").json()
    assert {j["kind"] for j in pending} == {"send_invite", "bid_reminder", "bid_close"}
    out = admin.post("/api/admin/clock/advance", json={"minutes": 15}).json()
    assert out["jobs_ran"] == 5 and "IST" in out["display"]
    nxt = admin.post("/api/admin/clock/next-event").json()
    assert nxt["jobs_ran"] >= 1
    assert admin.get("/api/admin/events").json()[0]["action"]


def test_admin_clock_needs_admin(login: Login) -> None:
    assert (
        login(SHARMA_OWNER).post("/api/admin/clock/advance", json={"minutes": 15}).status_code
        == 403
    )


def test_jobs_are_durable_rows(owner: TestClient, seeded: Session) -> None:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    assert len(seeded.scalars(select(Job).where(Job.status == "pending")).all()) == 7


def test_blocked_messages_are_not_in_the_vendor_inbox(
    owner: TestClient, seeded: Session, biz: DbDemoClock, login: Login
) -> None:
    biz.advance(timedelta(hours=7))
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    gupta = login(VENDOR_GUPTA)
    gupta.post("/api/vendor/messages", json={"client_message_id": "stop-2", "text": "STOP"})
    tick(biz, timedelta(hours=12))
    assert all(m["template"] != "rfq_invite" for m in gupta.get("/api/vendor/messages").json())
