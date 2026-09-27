"""Simulated WhatsApp channel: opt-out, 24 h window, templates, inbound dedupe, STOP."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import Outbound, OutsideWindow
from app.channels.inbound import Inbound, receive
from app.channels.simulated import get_channel
from app.channels.templates import TEMPLATES
from app.db.models import AuditLog, Message, Vendor
from app.jobs.clock import FixedClock
from tests.phones import VENDOR_BALAJI

T0 = datetime(2026, 9, 25, 8, 35, tzinfo=UTC)


@pytest.fixture
def vendor(seeded: Session) -> Vendor:
    return seeded.scalars(select(Vendor).where(Vendor.phone == VENDOR_BALAJI)).one()


def invite(vendor: Vendor) -> Outbound:
    params = {"builder": "B", "rfq_code": "RFQ-1", "item": "Cement", "qty": "30 bag", "area": "Noida",
              "needed_by": "5 Oct", "closes_at": "26 Sep"}  # fmt: skip
    return Outbound(vendor=vendor, template="rfq_invite", params=params)


def test_template_sent_and_delivered(seeded: Session, vendor: Vendor) -> None:
    m = get_channel().send(seeded, FixedClock(T0), invite(vendor))
    assert m.status == "sent" and m.delivered_at == T0 and "RFQ-1: Cement" in m.body and "RFQ RFQ" not in m.body
    assert m.payload["buttons"] == ["Submit quote"]


def test_opted_out_vendor_is_blocked_and_logged(seeded: Session, vendor: Vendor) -> None:
    vendor.opted_out_at = T0
    m = get_channel().send(seeded, FixedClock(T0), invite(vendor))
    assert m.status == "blocked" and m.delivered_at is None
    assert seeded.scalars(
        select(AuditLog).where(AuditLog.action == "message.blocked_opted_out")
    ).one()


def test_free_text_needs_open_window(seeded: Session, vendor: Vendor) -> None:
    clock = FixedClock(T0)
    with pytest.raises(OutsideWindow):
        get_channel().send(seeded, clock, Outbound(vendor=vendor, text="hello"))
    receive(seeded, clock, Inbound(vendor=vendor, client_message_id="in-1", text="rate 380"))
    m = get_channel().send(seeded, clock, Outbound(vendor=vendor, text="thanks"))
    assert m.in_24h_window is True
    clock.advance(timedelta(hours=24))
    with pytest.raises(OutsideWindow):
        get_channel().send(seeded, clock, Outbound(vendor=vendor, text="still there?"))


def test_hindi_vendor_gets_hindi_template(seeded: Session) -> None:
    singh = seeded.scalars(select(Vendor).where(Vendor.display_name == "Singh Cement Agency")).one()
    m = get_channel().send(seeded, FixedClock(T0), invite(singh))
    assert "कोटेशन" in m.body


def test_template_missing_params_rejected() -> None:
    with pytest.raises(KeyError, match="missing params"):
        TEMPLATES["rfq_invite"].render("en", {"builder": "B"})


def test_every_template_has_english_and_hindi() -> None:
    for t in TEMPLATES.values():
        assert set(t.text) == {"en", "hi"}, t.name
        hi_params = {p for p in t.params()}
        assert all("{" + p + "}" in t.text["hi"] for p in hi_params), t.name


def test_duplicate_inbound_is_stored_once(seeded: Session, vendor: Vendor) -> None:
    clock = FixedClock(T0)
    _, created = receive(
        seeded, clock, Inbound(vendor=vendor, client_message_id="dup-1", text="380")
    )
    _, again = receive(seeded, clock, Inbound(vendor=vendor, client_message_id="dup-1", text="380"))
    assert created and not again
    assert len(seeded.scalars(select(Message).where(Message.direction == "in")).all()) == 1


def test_out_of_order_arrival_keeps_device_time(seeded: Session, vendor: Vendor) -> None:
    clock = FixedClock(T0)
    receive(
        seeded,
        clock,
        Inbound(
            vendor=vendor,
            client_message_id="late",
            text="second",
            sent_at=T0 - timedelta(minutes=1),
        ),
    )
    receive(
        seeded,
        clock,
        Inbound(
            vendor=vendor,
            client_message_id="early",
            text="first",
            sent_at=T0 - timedelta(minutes=5),
        ),
    )
    rows = seeded.scalars(
        select(Message).where(Message.direction == "in").order_by(Message.sent_at)
    ).all()
    assert [m.body for m in rows] == ["first", "second"]


def test_device_time_in_future_is_capped(seeded: Session, vendor: Vendor) -> None:
    m, _ = receive(
        seeded,
        FixedClock(T0),
        Inbound(vendor=vendor, client_message_id="f", text="x", sent_at=T0 + timedelta(hours=3)),
    )
    assert m.sent_at == T0


@pytest.mark.parametrize("word", ["STOP", " stop ", "Unsubscribe", "बंद"])
def test_stop_opts_out_globally(seeded: Session, vendor: Vendor, word: str) -> None:
    clock = FixedClock(T0)
    receive(seeded, clock, Inbound(vendor=vendor, client_message_id="s", text=word))
    assert vendor.opted_out_at == T0
    confirm = seeded.scalars(
        select(Message).where(Message.template_name == "opt_out_confirm")
    ).one()
    assert confirm.status == "sent"  # the confirmation itself is allowed
    assert get_channel().send(seeded, clock, invite(vendor)).status == "blocked"


def test_start_opts_back_in(seeded: Session, vendor: Vendor) -> None:
    clock = FixedClock(T0)
    receive(seeded, clock, Inbound(vendor=vendor, client_message_id="s1", text="STOP"))
    receive(seeded, clock, Inbound(vendor=vendor, client_message_id="s2", text="start"))
    assert vendor.opted_out_at is None
    assert get_channel().send(seeded, clock, invite(vendor)).status == "sent"


def test_stop_inside_a_sentence_is_not_opt_out(seeded: Session, vendor: Vendor) -> None:
    receive(
        seeded,
        FixedClock(T0),
        Inbound(vendor=vendor, client_message_id="x", text="please stop by tomorrow"),
    )
    assert vendor.opted_out_at is None
