"""Quotation intake and parsing, end to end: vendor inbox -> parse job -> quote."""

import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import CatalogItem, Message, PriceHistory, Quote, Rfq
from app.llm.mock import get_mock
from app.seed.sample_docs import build as build_samples
from tests.conftest import DbDemoClock
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, VENDOR_BALAJI
from tests.test_outreach import messages, published_rfq, tick

Login = Callable[[str], TestClient]


@pytest.fixture(autouse=True)
def _mock() -> Iterator[None]:
    get_mock().reset()
    yield
    get_mock().reset()


@pytest.fixture
def owner(login: Login) -> TestClient:
    return login(SHARMA_OWNER)


@pytest.fixture
def vendor(login: Login) -> TestClient:
    return login(VENDOR_BALAJI)


@pytest.fixture
def rfq(owner: TestClient, seeded: Session, biz: DbDemoClock) -> dict[str, Any]:
    r = published_rfq(owner, seeded)
    owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)  # invites go out at 14:05 IST
    return r


def say(
    v: TestClient,
    rfq_id: str | None,
    text: str,
    button: str | None = None,
    reply_to: str | None = None,
) -> None:
    body = {
        "client_message_id": uuid.uuid4().hex,
        "text": text,
        "rfq_id": rfq_id,
        "button": button,
        "reply_to": reply_to,
    }
    assert v.post("/api/vendor/messages", json=body).status_code == 200


def send_file(v: TestClient, rfq_id: str | None, data: bytes, name: str) -> Any:
    q = f"/api/vendor/files?filename={name}&client_message_id={uuid.uuid4().hex}" + (
        f"&rfq_id={rfq_id}" if rfq_id else ""
    )
    return v.post(q, content=data)


def quotes(db: Session, rfq_id: str | None = None) -> list[Quote]:
    db.expire_all()
    q = select(Quote).order_by(Quote.revision)
    if rfq_id:
        q = q.where(Quote.rfq_id == uuid.UUID(rfq_id))
    return list(db.scalars(q))


def last_text(db: Session) -> str:
    return messages(db)[-1].body


SAMPLES = build_samples(date(2026, 9, 25))


# --- text and form ---------------------------------------------------------------------


def test_text_quote_needs_confirmation(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(
        vendor,
        rfq["rfq_id"],
        "rate 380 per bag, GST extra, delivery 1 Oct, valid till 10 Oct, 15 days credit",
    )
    tick(biz)
    (q,) = quotes(seeded)
    assert q.status == "awaiting_confirmation" and q.source == "text"
    assert (
        q.unit_price_paise == 38000
        and q.price_per_canonical_paise == 38000
        and q.gst_included is False
    )
    confirm = messages(seeded, "quote_confirm")[-1]
    assert "₹380.00 per bag" in confirm.body and "GST extra" in confirm.body
    assert confirm.payload["buttons"] == ["Yes", "Edit"]
    say(vendor, rfq["rfq_id"], "Yes", button="Yes")
    (q,) = quotes(seeded)
    assert q.status == "confirmed" and q.confirmed_by_vendor_at is not None


def test_confirm_marks_invitation_responded(
    owner: TestClient, vendor: TestClient, rfq: dict[str, Any], biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "rate 380 per bag + GST")
    tick(biz)
    say(vendor, rfq["rfq_id"], "Yes", button="Yes")
    row = next(
        s
        for s in owner.get(f"/api/rfqs/{rfq['rfq_id']}").json()["shortlist"]
        if s["vendor"] == "Shree Balaji Cement Traders"
    )
    assert row["status"] == "responded"


def test_hinglish_quote(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "380 ka rate hai bag ka, gst alag, 3 din mein supply")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.unit_price_paise == 38000 and q.gst_included is False and q.delivery_date is not None


def form(rfq_id: str, **over: Any) -> dict[str, Any]:
    base = {"client_message_id": uuid.uuid4().hex, "rfq_id": rfq_id, "unit_price": "385", "price_unit": "bag",
            "gst_included": False, "gst_percent": "18", "delivery_date": "2026-10-01", "validity_until": "2026-10-15",
            "payment_terms_days": 15}  # fmt: skip
    return {**base, **over}


def test_form_quote_confirmed_directly(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session
) -> None:
    r = vendor.post("/api/vendor/quotes", json=form(rfq["rfq_id"]))
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert get_mock().calls == []  # structured form: no LLM


def test_form_submission_is_idempotent(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session
) -> None:
    body = form(rfq["rfq_id"])
    a, b = (
        vendor.post("/api/vendor/quotes", json=body).json(),
        vendor.post("/api/vendor/quotes", json=body).json(),
    )
    assert a["quote_id"] == b["quote_id"] and len(quotes(seeded)) == 1


def test_form_validates_input(vendor: TestClient, rfq: dict[str, Any]) -> None:
    assert (
        vendor.post("/api/vendor/quotes", json=form(rfq["rfq_id"], unit_price="-5")).status_code
        == 422
    )
    assert (
        vendor.post("/api/vendor/quotes", json=form(rfq["rfq_id"], price_unit="truck")).status_code
        == 422
    )


# --- documents ---------------------------------------------------------------------------


def test_clean_pdf(
    owner: TestClient, vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], SAMPLES["clean"].data, "quote.pdf")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.source == "pdf" and q.unit_price_paise == 38500 and q.brand == "UltraTech"
    assert "arithmetic_mismatch" not in q.flags and "suspicious_content" not in q.flags
    listed = owner.get(f"/api/rfqs/{rfq['rfq_id']}/quotes").json()[0]
    assert (
        listed["has_file"]
        and owner.get(f"/api/quotes/{q.id}/file").content == SAMPLES["clean"].data
    )


def test_arithmetic_mismatch_flagged_and_vendor_told(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], SAMPLES["arithmetic"].data, "q.pdf")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.flags["arithmetic_mismatch"]["stated_total"] == "₹12,500.00"
    assert any("does not match" in m.body for m in messages(seeded))


def test_rate_list_keeps_only_the_rfq_item(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], SAMPLES["ratelist"].data, "rates.pdf")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.unit_price_paise == 39500 and q.flags["rate_list"] == {
        "rows": 6,
        "picked": "OPC 53 Grade Cement",
    }


@pytest.mark.parametrize("name", ["scanned", "scanned_pdf"])
def test_scanned_documents_go_multimodal(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock, name: str
) -> None:
    send_file(vendor, rfq["rfq_id"], SAMPLES[name].data, SAMPLES[name].filename)
    tick(biz)
    (q,) = quotes(seeded)
    assert q.unit_price_paise == 37200 and q.brand == "Ambuja"
    assert get_mock().calls[-1].attachments, "scan must be sent to the model as a file"


def test_hidden_instructions_are_just_data(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], SAMPLES["hidden"].data, "q.pdf")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.unit_price_paise == 40000  # the visible rate, not the injected ₹500
    assert q.status == "awaiting_confirmation"  # nothing auto-accepted
    assert "suspicious_content" in q.flags
    seeded.expire_all()
    assert seeded.get(Rfq, uuid.UUID(rfq["rfq_id"])).status == "bidding"  # type: ignore[union-attr]
    call = get_mock().calls[-1]
    assert "untrusted data" in call.system


def test_unreadable_photo_asks_to_resend(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, "blurry.png")
    tick(biz)
    assert quotes(seeded) == [] and "could not read" in last_text(seeded)


def test_wrong_file_type(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], b"PK\x03\x04 a zip", "quote.docx")
    tick(biz)
    assert quotes(seeded) == [] and "PDF or a photo" in last_text(seeded)


def test_file_too_large(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    r = send_file(vendor, rfq["rfq_id"], b"%PDF" + b"0" * (10 * 1024 * 1024), "huge.pdf")
    tick(biz)
    assert (
        r.status_code == 200 and quotes(seeded) == [] and "larger than 10 MB" in last_text(seeded)
    )


def test_corrupt_pdf_is_unreadable(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], b"%PDF-1.4 garbage", "broken.pdf")
    tick(biz)
    assert quotes(seeded) == [] and "could not read" in last_text(seeded)


# --- business checks -------------------------------------------------------------------


def test_per_tonne_quote_converted_to_per_bag(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "Rs 7,600 per MT + GST, delivery 1 Oct, valid till 12 Oct")
    tick(biz)
    (q,) = quotes(seeded)
    assert (
        q.price_unit == "tonne"
        and q.unit_price_paise == 760000
        and q.price_per_canonical_paise == 38000
    )
    assert "₹380.00 per bag" in q.flags["converted"]


def test_gst_included_vs_excluded(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "395 per bag inclusive of GST 18%")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.gst_included is True and q.gst_bp == 1800


def test_missing_validity_defaults_and_flags(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "380 per bag + GST, delivery 1 Oct")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.flags["validity_defaulted"] == "2026-10-02" and q.validity_until == date(2026, 10, 2)


def test_short_validity_flagged(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "380 per bag + GST, valid till 26 Sep")
    tick(biz)
    assert "validity_short" in quotes(seeded)[0].flags


def test_outlier_price_needs_confirmation(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    item = seeded.scalars(select(CatalogItem).where(CatalogItem.code == "OPC53")).one()
    for p in (38500, 39000, 39500):
        seeded.add(
            PriceHistory(
                catalog_item_id=item.id,
                region="201",
                unit_price_paise=p,
                landed_paise=p,
                price_date=date(2026, 9, 1),
            )
        )
    seeded.commit()
    r = vendor.post(
        "/api/vendor/quotes", json=form(rfq["rfq_id"], unit_price="3900")
    )  # typo: 3900 for 390
    assert r.json()["status"] == "awaiting_confirmation"
    assert r.json()["flags"]["possible_typo"]["deviation_pct"] == 900.0


def test_quote_after_window_closed_is_recorded_not_ranked(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    tick(biz, timedelta(days=1, minutes=5))
    say(vendor, rfq["rfq_id"], "380 per bag + GST")
    tick(biz)
    (q,) = quotes(seeded)
    assert q.status == "rejected" and "late" in q.flags
    assert "cannot be considered" in last_text(seeded)


def test_second_quote_supersedes_first_and_history_is_kept(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    vendor.post("/api/vendor/quotes", json=form(rfq["rfq_id"], unit_price="395"))
    vendor.post("/api/vendor/quotes", json=form(rfq["rfq_id"], unit_price="385"))
    first, second = quotes(seeded)
    assert (first.revision, first.status) == (1, "superseded")
    assert (second.revision, second.status, second.unit_price_paise) == (2, "confirmed", 38500)


def test_edit_button_reopens_the_quote(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "380 per bag")
    tick(biz)
    say(vendor, rfq["rfq_id"], "Edit", button="Edit")
    assert quotes(seeded)[0].status == "draft_parsed" and "corrected rate" in last_text(seeded)


def test_withdraw(vendor: TestClient, rfq: dict[str, Any], seeded: Session) -> None:
    vendor.post("/api/vendor/quotes", json=form(rfq["rfq_id"]))
    say(vendor, rfq["rfq_id"], "withdraw")
    assert quotes(seeded)[0].status == "withdrawn"


def test_ambiguous_rfq_asks_which_one(
    owner: TestClient, vendor: TestClient, seeded: Session, biz: DbDemoClock
) -> None:
    a = published_rfq(owner, seeded)
    b = published_rfq(owner, seeded, qty="60")
    for r in (a, b):
        owner.post(f"/api/rfqs/{r['rfq_id']}/send")
    tick(biz)
    send_file(vendor, None, SAMPLES["clean"].data, "quote.pdf")  # sent outside either conversation
    tick(biz)
    assert quotes(seeded) == []
    picker = messages(seeded)[-1]
    assert picker.payload["list"] and len(picker.payload["buttons"]) == 2
    b_code = seeded.get(Rfq, uuid.UUID(b["rfq_id"])).public_code  # type: ignore[union-attr]
    say(vendor, None, b_code, button=b_code, reply_to=str(picker.id))
    tick(biz)
    (q,) = quotes(seeded)
    assert str(q.rfq_id) == b["rfq_id"]


def test_single_open_rfq_needs_no_question(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, None, SAMPLES["clean"].data, "quote.pdf")
    tick(biz)
    assert str(quotes(seeded)[0].rfq_id) == rfq["rfq_id"]


# --- LLM guard rails -----------------------------------------------------------------------


def test_invalid_llm_json_retries_then_falls_back(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    get_mock().script("parse_quote", "not json at all", '{"unit_price": "abc"}')
    say(vendor, rfq["rfq_id"], "380 per bag + GST")
    tick(biz)
    assert len(get_mock().calls) == 2  # one try + one retry
    assert quotes(seeded)[0].unit_price_paise == 38000  # deterministic fallback


def test_llm_extra_fields_rejected(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    get_mock().script(
        "parse_quote",
        '{"unit_price": "1", "price_unit": "bag", "action": "approve"}',
        '{"unit_price": "1", "price_unit": "bag", "action": "approve"}',
    )
    say(vendor, rfq["rfq_id"], "380 per bag + GST")
    tick(biz)
    assert quotes(seeded)[0].unit_price_paise == 38000  # schema forbids extra fields -> fallback


def test_target_and_max_price_never_reach_the_llm(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    seeded.execute(
        update(Rfq)
        .where(Rfq.id == uuid.UUID(rfq["rfq_id"]))
        .values(target_price_paise=36123, max_price_paise=41987)
    )
    seeded.commit()
    say(vendor, rfq["rfq_id"], "380 per bag + GST")
    send_file(vendor, rfq["rfq_id"], SAMPLES["clean"].data, "q.pdf")
    tick(biz)
    assert get_mock().calls
    for call in get_mock().calls:
        blob = call.system + call.prompt
        for secret in ("36123", "361.23", "41987", "419.87"):
            assert secret not in blob


# --- builder side ----------------------------------------------------------------------------


def test_quotes_cross_tenant_404(
    login: Login, vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    send_file(vendor, rfq["rfq_id"], SAMPLES["clean"].data, "q.pdf")
    tick(biz)
    q = quotes(seeded)[0]
    other = login(GREENLINE_OWNER)
    assert other.get(f"/api/rfqs/{rfq['rfq_id']}/quotes").status_code == 404
    assert other.get(f"/api/quotes/{q.id}/file").status_code == 404


def test_vendor_cannot_quote_on_rfq_not_invited(login: Login, rfq: dict[str, Any]) -> None:
    singh = login("+919000020003")
    assert singh.post("/api/vendor/quotes", json=form(rfq["rfq_id"])).status_code == 404


def test_samples_listed(vendor: TestClient) -> None:
    names = {s["name"] for s in vendor.get("/api/vendor/samples").json()}
    assert names == {"clean", "arithmetic", "ratelist", "scanned", "scanned_pdf", "hidden"}


def test_every_parse_is_idempotent(
    vendor: TestClient, rfq: dict[str, Any], seeded: Session, biz: DbDemoClock
) -> None:
    say(vendor, rfq["rfq_id"], "380 per bag + GST")
    tick(biz)
    msg = seeded.scalars(select(Message).where(Message.direction == "in")).first()
    assert msg is not None
    from app.agents.quote_intake import parse_quote_job
    from app.jobs.demo_clock import load_clock

    parse_quote_job(
        seeded, load_clock(seeded), {"message_id": str(msg.id)}
    )  # job re-run after a crash
    assert len(quotes(seeded)) == 1
