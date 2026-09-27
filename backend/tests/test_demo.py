"""`make reset && make demo` must bring every scenario to its demo point (Stage 12)."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CapacityReservation, NegotiationThread, Quote, Vendor
from app.demo.autoreply import settings
from app.demo.load import run
from app.demo.scenarios import ADMIN, ARORA, GREENLINE, SHARMA, Driver
from app.seed.run import recompute_ratings, seed_history
from tests.conftest import DbDemoClock


@pytest.fixture
def loaded(seeded: Session, biz: DbDemoClock) -> dict[str, Any]:
    seed_history(seeded, biz)  # as `make reset`: history shapes vendor ratings, which decide L1
    recompute_ratings(seeded)
    seeded.commit()
    return run(["1", "2", "3", "4", "5", "6", "7"])


def comparison(d: Driver, owner: str, rfq: str) -> dict[str, Any]:
    out: dict[str, Any] = d.call(owner, "GET", f"/rfqs/{rfq}/comparison")
    return out


def test_every_scenario_reaches_its_demo_point(loaded: dict[str, Any], seeded: Session) -> None:
    d = Driver()
    states = {k: v["status"] for k, v in loaded.items()}
    assert states == {"1": "awaiting_approval", "2": "awaiting_approval", "3": "bidding", "4": "awaiting_approval",
                      "5": "awaiting_approval", "6": "awaiting_approval", "7": "awarded"}  # fmt: skip

    # 1. Happy path: L1 at ₹380 after three rounds
    rec = comparison(d, SHARMA, loaded["1"]["rfq"])["recommendation"]
    l1 = next(r for r in rec["ranked"] if r["vendor_id"] == rec["l1_vendor_id"])
    assert l1["vendor"] == "Delhi Cement Depot" and l1["landed_paise"] == 44840  # ₹380 + 18% GST
    threads = d.call(SHARMA, "GET", f"/rfqs/{loaded['1']['rfq']}/negotiations")
    assert max(t["round"] for t in threads) == 3

    # 2. Big order: complete split
    split = comparison(d, SHARMA, loaded["2"]["rfq"])["recommendation"]["split_proposal"]
    assert split and split["shortfall_milli"] == 0 and len(split["allocations"]) >= 2

    # 3. PDF quotes: five documents, flagged as expected
    quotes = d.call(ARORA, "GET", f"/rfqs/{loaded['3']['rfq']}/quotes")
    flags = {q["vendor"]: set(q["flags"]) for q in quotes}
    assert "arithmetic_mismatch" in flags["Delhi Cement Depot"]
    assert "suspicious_content" in flags["Singh Cement Agency"]
    assert "rate_list" in flags["Shree Balaji Cement Traders"]
    assert {q["source"] for q in quotes} >= {"pdf", "photo"}

    # 4. Capacity conflict: Sharma's PO to Gupta confirmed; Greenline's approval will be blocked
    gupta = seeded.scalars(select(Vendor).where(Vendor.phone == "+919000020002")).one()
    assert (
        sum(
            r.qty_reserved_milli
            for r in seeded.scalars(
                select(CapacityReservation).where(CapacityReservation.vendor_id == gupta.id)
            )
        )
        == 600_000
    )
    rec4 = comparison(d, GREENLINE, loaded["4"]["rfq"])["recommendation"]
    assert rec4["l1_vendor_id"] == str(gupta.id)

    # 5. Handoff
    t5 = seeded.scalars(
        select(NegotiationThread).where(
            NegotiationThread.handoff_reason == "Vendor asked for a phone call"
        )
    ).one()
    assert str(t5.rfq_id) == loaded["5"]["rfq"]

    # 6. Timeout: runner-up offered
    view6 = d.call(ARORA, "GET", f"/rfqs/{loaded['6']['rfq']}")
    assert (
        view6["match_report"]["runner_up"]
        and "did not confirm" in view6["match_report"]["runner_up_reason"]
    )

    # 7. Short delivery + invoice mismatch
    wo = d.call(SHARMA, "GET", f"/work-orders/{loaded['7']['work_order']}")
    assert wo["status"] == "in_delivery"
    assert "short_delivery" in wo["deliveries"][0]["flags"]
    assert wo["invoices"][0]["status"] == "flagged" and {"unit_price", "amount"} <= set(
        wo["invoices"][0]["mismatch_flags"]
    )

    # the hidden-instruction PDF was read at its visible ₹400, not the injected ₹500
    singh = seeded.scalars(select(Vendor).where(Vendor.phone == "+919000020003")).one()
    hidden = seeded.scalars(
        select(Quote).where(
            Quote.vendor_id == singh.id, Quote.rfq_id == uuid.UUID(loaded["3"]["rfq"])
        )
    ).one()
    assert hidden.unit_price_paise == 40000 and hidden.status == "awaiting_confirmation"
    # live demo afterwards: vendors answer by themselves
    assert settings(seeded)["auto_reply"] is True
    assert d.call(ADMIN, "GET", "/admin/clock")["display"]


def test_capacity_conflict_blocks_second_builder(loaded: dict[str, Any], seeded: Session) -> None:
    d = Driver()
    rfq = loaded["4"]["rfq"]
    v = d.call(GREENLINE, "GET", f"/rfqs/{rfq}")["version"]
    r = d.as_(GREENLINE).post(
        f"/api/rfqs/{rfq}/approve",
        json={"idempotency_key": "demo-conflict-1", "choice": "l1", "version": v},
    )
    assert r.status_code == 409 and "no longer has capacity" in r.json()["detail"]


def test_play_runs_scenario_one_to_a_closed_order(seeded: Session, biz: DbDemoClock) -> None:
    out = run([], play=True)
    assert out["play"]["status"] == "closed"


def test_personas_reply_by_themselves(seeded: Session, biz: DbDemoClock) -> None:
    from app.demo.autoreply import set_settings
    from tests.test_outreach import tick

    set_settings(seeded, auto_reply=True)
    seeded.commit()
    d = Driver()
    rfq = d.publish(
        SHARMA, "Noida Sector 62 Tower", "OPC 53 Grade Cement", "30", "bag", "persona test"
    )
    d.send(SHARMA, rfq)
    tick(biz, timedelta(minutes=35))  # quote, confirm prompt, "Yes"
    quotes = {q["vendor"]: q for q in d.call(SHARMA, "GET", f"/rfqs/{rfq}/quotes")}
    assert quotes["Shree Balaji Cement Traders"]["status"] == "confirmed"  # cooperative
    assert "Yadav Cement Store" not in quotes  # slow: never answers
    assert "Delhi Cement Depot" not in quotes  # vague: "rate kal tak bhejta hu"


def test_demo_panel_settings_and_personas(login: Callable[[str], Any], seeded: Session) -> None:
    admin = login(ADMIN)
    state = admin.get("/api/admin/demo").json()
    assert {s["key"] for s in state["scenarios"]} == {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    } and "injection" in state["personas"]
    assert (
        admin.put(
            "/api/admin/demo/settings", json={"persona_mode": "gemini", "auto_reply": False}
        ).json()["persona_mode"]
        == "gemini"
    )
    v = admin.get("/api/admin/vendors").json()[0]
    assert (
        admin.put(f"/api/admin/vendors/{v['id']}/persona", json={"persona": "stubborn"}).json()[
            "persona"
        ]
        == "stubborn"
    )
    assert (
        admin.put(f"/api/admin/vendors/{v['id']}/persona", json={"persona": "angry"}).status_code
        == 422
    )
    assert admin.post("/api/admin/demo/load", json={"scenarios": ["9"]}).status_code == 422
    assert login(SHARMA).get("/api/admin/demo").status_code == 403
