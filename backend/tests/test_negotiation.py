"""Negotiation agent: every persona and rule in PROMPT.md 10.5 / Stage 9."""

import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.personas import reply as persona_reply
from app.db.models import Message, NegotiationThread, Quote, Vendor
from app.llm.mock import get_mock
from tests.conftest import DbDemoClock
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, SHARMA_SE
from tests.test_outreach import published_rfq, tick

Login = Callable[[str], TestClient]
BALAJI, GUPTA, DELHI = "+919000020001", "+919000020002", "+919000020004"
NAMES = {
    BALAJI: "Shree Balaji Cement Traders",
    GUPTA: "Gupta Building Materials",
    DELHI: "Delhi Cement Depot",
}
DEBOUNCE = timedelta(seconds=46)


@pytest.fixture(autouse=True)
def _mock() -> Iterator[None]:
    get_mock().reset()
    yield
    get_mock().reset()


def form(rfq_id: str, price: str) -> dict[str, Any]:
    return {"client_message_id": uuid.uuid4().hex, "rfq_id": rfq_id, "unit_price": price, "price_unit": "bag",
            "gst_included": False, "gst_percent": "18", "delivery_date": "2026-10-01", "validity_until": "2026-10-20",
            "payment_terms_days": 15}  # fmt: skip


@pytest.fixture
def owner(login: Login) -> TestClient:
    return login(SHARMA_OWNER)


def negotiating(
    owner: TestClient,
    login: Login,
    db: Session,
    biz: DbDemoClock,
    *,
    target: int | None = None,
    max_price: int | None = None,
) -> str:
    """Three confirmed quotes (Balaji 385, Gupta 390, Delhi 400), bid closed, negotiation started."""
    r = published_rfq(owner, db)
    rfq: str = r["rfq_id"]
    owner.post(f"/api/rfqs/{rfq}/send")
    tick(biz)
    for phone, price in ((BALAJI, "385"), (GUPTA, "390"), (DELHI, "400")):
        assert (
            login(phone).post("/api/vendor/quotes", json=form(rfq, price)).json()["status"]
            == "confirmed"
        )
    if target or max_price:
        owner.put(
            f"/api/rfqs/{rfq}/limits",
            json={"target_price_paise": target, "max_price_paise": max_price},
        )
    tick(biz, timedelta(hours=24, minutes=1))
    return rfq


def thread(db: Session, rfq: str, phone: str) -> NegotiationThread:
    db.expire_all()
    v = db.scalars(select(Vendor).where(Vendor.phone == phone)).one()
    return db.scalars(
        select(NegotiationThread).where(
            NegotiationThread.rfq_id == uuid.UUID(rfq), NegotiationThread.vendor_id == v.id
        )
    ).one()


def outbound(db: Session, t: NegotiationThread) -> list[Message]:
    db.expire_all()
    return list(
        db.scalars(
            select(Message)
            .where(Message.thread_id == t.id, Message.direction == "out")
            .order_by(Message.sent_at, Message.created_at)
        )
    )


def say(login: Login, phone: str, rfq: str, text: str, reply_to: str | None = None) -> None:
    body = {
        "client_message_id": uuid.uuid4().hex,
        "text": text,
        "rfq_id": rfq,
        "reply_to": reply_to,
    }
    assert login(phone).post("/api/vendor/messages", json=body).status_code == 200


def play(
    login: Login, db: Session, biz: DbDemoClock, rfq: str, phone: str, persona: str, current: int
) -> str | None:
    """The persona answers the agent's latest message; the debounce window then passes."""
    t = thread(db, rfq, phone)
    text = persona_reply(persona, outbound(db, t)[-1].body, current, t.round)
    if text is not None:
        say(login, phone, rfq, text)
    tick(biz, DEBOUNCE)
    return text


# --- start ---------------------------------------------------------------------------------


def test_negotiation_starts_with_disclosed_automation(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    assert owner.get(f"/api/rfqs/{rfq}").json()["status"] == "negotiating"
    threads = owner.get(f"/api/rfqs/{rfq}/negotiations").json()
    assert len(threads) == 3 and {t["state"] for t in threads} == {"awaiting_reply"}
    for t in threads:
        first = next(m for m in t["messages"] if m["direction"] == "out")
        assert first["body"].startswith("Automated assistant for Sharma Constructions.")


def test_counters_follow_the_pricing_engine(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    balaji, gupta = thread(seeded, rfq, BALAJI), thread(seeded, rfq, GUPTA)
    assert (
        "₹374 per bag" in outbound(seeded, balaji)[-1].body
    )  # benchmark: 3% better (385 * 0.97, rounded up)
    assert "₹385 per bag" in outbound(seeded, gupta)[-1].body  # others: match the benchmark


# --- personas --------------------------------------------------------------------------------


def test_cooperative_reaches_target_and_stops_early(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz, target=44500)  # landed ₹445 incl. GST
    play(login, seeded, biz, rfq, BALAJI, "cooperative", 385)
    t = thread(seeded, rfq, BALAJI)
    assert t.state == "closed" and t.round == 1  # stopped after one round
    assert t.current_offer_paise == 44132  # ₹374 + 18% GST
    q = seeded.scalars(
        select(Quote).where(Quote.vendor_id == t.vendor_id, Quote.status == "confirmed")
    ).one()
    assert q.unit_price_paise == 37400 and q.source == "negotiation"


def test_stubborn_ends_after_round_three(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    price = 390
    for _ in range(3):
        play(login, seeded, biz, rfq, GUPTA, "stubborn", price)
        price -= 1
    t = thread(seeded, rfq, GUPTA)
    assert t.state == "closed" and t.round == 3
    assert (
        "best and final" in outbound(seeded, t)[-1].body.lower()
    )  # the final "no discount" closes silently


def test_vague_twice_goes_to_a_human(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    play(login, seeded, biz, rfq, DELHI, "vague", 400)
    t = thread(seeded, rfq, DELHI)
    assert t.state == "awaiting_reply" and "did not follow" in outbound(seeded, t)[-1].body
    play(login, seeded, biz, rfq, DELHI, "vague", 400)
    t = thread(seeded, rfq, DELHI)
    assert t.state == "needs_human" and t.handoff_reason == "Two unclear replies"


def test_injection_has_no_effect_on_price_or_state(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    before = thread(seeded, rfq, GUPTA).current_offer_paise
    play(login, seeded, biz, rfq, GUPTA, "injection", 390)
    t = thread(seeded, rfq, GUPTA)
    assert t.current_offer_paise == before and t.state in ("awaiting_reply", "needs_human")
    assert not seeded.scalars(select(Quote).where(Quote.unit_price_paise == 50000)).first()
    assert owner.get(f"/api/rfqs/{rfq}").json()["status"] == "negotiating"


def test_term_change_rescores_and_never_auto_accepts(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    play(login, seeded, biz, rfq, GUPTA, "term_changer", 390)
    t = thread(seeded, rfq, GUPTA)
    assert t.state == "needs_human" and "delivery_date" in (t.handoff_reason or "")
    assert not seeded.scalars(
        select(Quote).where(Quote.vendor_id == t.vendor_id, Quote.source == "negotiation")
    ).first()


def test_caller_goes_to_a_human(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    play(login, seeded, biz, rfq, DELHI, "caller", 400)
    assert thread(seeded, rfq, DELHI).handoff_reason == "Vendor asked for a phone call"


def test_slow_vendor_nudged_then_timed_out(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    tick(biz, timedelta(hours=3, minutes=1))  # 3 working hours (15:06 -> 18:07 IST)
    t = thread(seeded, rfq, DELHI)
    assert t.nudged and "reply to our last message" in outbound(seeded, t)[-1].body
    assert t.state == "awaiting_reply"
    tick(biz, timedelta(hours=18))  # next 3 working hours pass overnight
    assert thread(seeded, rfq, DELHI).state == "closed"


def test_ok_is_best_and_final_not_a_deal(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    say(login, GUPTA, rfq, "ok")
    tick(biz, DEBOUNCE)
    t = thread(seeded, rfq, GUPTA)
    assert t.state == "closed" and t.current_offer_paise == 45430  # agreed to our ₹385 counter
    assert owner.get(f"/api/rfqs/{rfq}").json()["status"] in (
        "negotiating",
        "awaiting_approval",
    )  # never awarded


def test_reject_keeps_last_offer(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    say(login, DELHI, rfq, "No discount possible")
    tick(biz, DEBOUNCE)
    t = thread(seeded, rfq, DELHI)
    assert t.state == "closed" and t.current_offer_paise == t.opening_offer_paise


def test_all_threads_done_goes_to_approval(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    for phone in (BALAJI, GUPTA, DELHI):
        say(login, phone, rfq, "No discount possible")
    tick(biz, DEBOUNCE)
    assert owner.get(f"/api/rfqs/{rfq}").json()["status"] == "awaiting_approval"


# --- rules ---------------------------------------------------------------------------------


def test_offer_above_max_is_not_recommended(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz, max_price=46000)  # ₹400 lands at ₹472
    for phone in (BALAJI, GUPTA, DELHI):
        say(login, phone, rfq, "No discount possible")
    tick(biz, DEBOUNCE)
    rec = owner.get(f"/api/rfqs/{rfq}/comparison").json()["recommendation"]
    delhi = next(r for r in rec["ranked"] if r["vendor"] == NAMES[DELHI])
    assert delhi["above_max"] and rec["l1_vendor_id"] != delhi["vendor_id"]


def test_invalid_reply_json_retries_then_falls_back(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    get_mock().script("parse_reply", "not json", '{"intent": "approve"}')
    say(login, GUPTA, rfq, "No discount possible")
    tick(biz, DEBOUNCE)
    t = thread(seeded, rfq, GUPTA)
    assert t.parse_fail_count == 1 and t.state == "closed"  # deterministic reader understood it


def test_two_parse_failures_go_to_a_human(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    get_mock().script("parse_reply", "x", "y", "x", "y")
    say(login, DELHI, rfq, "dekh lenge")
    tick(biz, DEBOUNCE)
    say(login, DELHI, rfq, "sochta hu")
    tick(biz, DEBOUNCE)
    t = thread(seeded, rfq, DELHI)
    assert t.state == "needs_human" and "Could not read" in (t.handoff_reason or "")


def test_writer_wrong_number_caught_by_validator(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    get_mock().script("write_message", "Could you do ₹350 per bag?", "Final: ₹360 per bag please")
    say(login, GUPTA, rfq, "388 final")
    tick(biz, DEBOUNCE)
    last = outbound(seeded, thread(seeded, rfq, GUPTA))[-1].body
    assert "350" not in last and "360" not in last and "₹385 per bag" in last  # template fallback


def test_builder_take_over_stops_the_agent(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    t = thread(seeded, rfq, GUPTA)
    out = owner.post(f"/api/negotiations/{t.id}/take-over").json()
    assert out["state"] == "needs_human" and out["taken_over"]
    sent_before = len(outbound(seeded, t))
    say(login, GUPTA, rfq, "388 final")
    tick(biz, DEBOUNCE)
    tick(biz, timedelta(hours=8))  # no nudges either
    assert len(outbound(seeded, t)) == sent_before
    msg = owner.post(
        f"/api/negotiations/{t.id}/message", json={"text": "Can you do 386 if we pay in 7 days?"}
    ).json()
    assert msg["messages"][-1]["by_human"] is True


def test_manual_message_requires_take_over(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    t = thread(seeded, rfq, GUPTA)
    assert owner.post(f"/api/negotiations/{t.id}/message", json={"text": "hi"}).status_code == 409


def test_deadline_mid_round_closes_everything(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    tick(biz, timedelta(hours=24, minutes=5))
    states = {t["state"] for t in owner.get(f"/api/rfqs/{rfq}/negotiations").json()}
    assert states == {"closed"}
    assert owner.get(f"/api/rfqs/{rfq}").json()["status"] == "awaiting_approval"


def test_burst_replies_merged_into_one_parse(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    get_mock().reset()
    say(login, GUPTA, rfq, "hmm")
    say(login, GUPTA, rfq, "388 final")
    tick(biz, DEBOUNCE)
    assert [c.task for c in get_mock().calls].count("parse_reply") == 1
    assert "hmm" in get_mock().calls[0].prompt and "388 final" in get_mock().calls[0].prompt


def test_stale_reply_recorded_but_ignored(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    first_counter = outbound(seeded, thread(seeded, rfq, GUPTA))[-1]
    play(login, seeded, biz, rfq, GUPTA, "stubborn", 390)  # round 2 counter goes out
    before = thread(seeded, rfq, GUPTA).current_offer_paise
    say(login, GUPTA, rfq, "ok 300 done", reply_to=str(first_counter.id))  # answers round 1
    tick(biz, DEBOUNCE)
    t = thread(seeded, rfq, GUPTA)
    assert t.current_offer_paise == before and t.round == 2
    stale = seeded.scalars(select(Message).where(Message.body == "ok 300 done")).one()
    assert stale.payload.get("stale") is True


def test_disclosure_lower_offer_first_then_price(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    say(login, DELHI, rfq, "what is the lower rate you have?")
    tick(biz, DEBOUNCE)
    t = thread(seeded, rfq, DELHI)
    assert outbound(seeded, t)[-1].body.startswith("We have received a lower offer.")
    say(login, DELHI, rfq, "kitna kam rate mila hai dusre se?")
    tick(biz, DEBOUNCE)
    last = outbound(seeded, thread(seeded, rfq, DELHI))[-1].body
    assert "₹385 per bag" in last
    for name in NAMES.values():
        assert name not in last


def test_benchmark_vendor_is_not_told_of_a_lower_offer(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    say(login, BALAJI, rfq, "what is the lower rate you have?")
    tick(biz, DEBOUNCE)
    assert "currently among the best" in outbound(seeded, thread(seeded, rfq, BALAJI))[-1].body


def test_target_and_max_never_in_any_prompt(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz, target=43987, max_price=48123)
    for phone, persona, cur in (
        (BALAJI, "cooperative", 385),
        (GUPTA, "hinglish", 390),
        (DELHI, "vague", 400),
    ):
        play(login, seeded, biz, rfq, phone, persona, cur)
    assert get_mock().calls
    for call in get_mock().calls:
        blob = call.system + call.prompt
        for secret in ("43987", "439.87", "48123", "481.23"):
            assert secret not in blob
        if call.task == "write_message":
            others = [n for n in NAMES.values() if f"Supplier: {n}" not in call.prompt]
            assert not any(n in call.prompt for n in others)  # never another vendor's name


# --- permissions & tenancy ---------------------------------------------------------------


def test_take_over_permissions_and_tenancy(
    owner: TestClient, login: Login, seeded: Session, biz: DbDemoClock
) -> None:
    rfq = negotiating(owner, login, seeded, biz)
    t = thread(seeded, rfq, GUPTA)
    assert login(SHARMA_SE).post(f"/api/negotiations/{t.id}/take-over").status_code == 403
    other = login(GREENLINE_OWNER)
    assert other.post(f"/api/negotiations/{t.id}/take-over").status_code == 404
    assert other.get(f"/api/rfqs/{rfq}/negotiations").status_code == 404
