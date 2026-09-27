"""The seven demo scenarios (PROMPT.md 13), driven through the real API in-process.

Each scenario acts exactly like the people involved would: builders publish and approve,
vendors quote and reply from their inbox, the admin moves the demo clock. So every rule,
check and message in the product applies. Each stops at its "demo point" (see
docs/DEMO_SCRIPT.md). Loaded in an order that keeps later clock jumps from disturbing
earlier scenarios.
"""

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import COOKIE, issue_token
from app.auth.otp import find_subject
from app.db.models import Site, Vendor
from app.db.session import get_engine
from app.jobs.clock import SystemClock, ist_today
from app.jobs.demo_clock import load_clock

ADMIN = "+919000000001"
SHARMA, SHARMA_SE = "+919000010001", "+919000010003"
GREENLINE = "+919000010011"
ARORA = "+919000010021"
BALAJI, GUPTA, SINGH, DELHI, MAHALAXMI, YADAV = (f"+9190000200{n:02d}" for n in (1, 2, 3, 4, 5, 6))
BANSAL, GOYAL = "+919000020008", "+919000020009"


class DemoError(RuntimeError):
    pass


class Driver:
    def __init__(self) -> None:
        self._clients: dict[str, TestClient] = {}

    def as_(self, phone: str) -> TestClient:
        if phone not in self._clients:
            with Session(get_engine()) as db:
                subject = find_subject(db, phone)
                assert subject is not None, phone
                token = issue_token(db, subject[0], subject[1], SystemClock())
            from app.main import app  # imported here: the admin API imports this module

            self._clients[phone] = TestClient(app, cookies={COOKIE: token})
        return self._clients[phone]

    def call(self, phone: str, method: str, path: str, **kw: Any) -> Any:  # noqa: ANN401
        r = self.as_(phone).request(method, f"/api{path}", **kw)
        if r.status_code >= 400:
            raise DemoError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    # --- time -------------------------------------------------------------------------

    def today(self) -> date:
        with Session(get_engine()) as db:
            return ist_today(load_clock(db))

    def advance(self, minutes: int) -> None:
        self.call(ADMIN, "POST", "/admin/clock/advance", json={"minutes": minutes})

    def settle(self) -> None:
        """Let the 45-second reply debounce pass and the agent answer."""
        self.advance(1)

    # --- builder steps -----------------------------------------------------------------

    def publish(
        self,
        owner: str,
        site: str,
        item: str,
        qty: str,
        unit: str,
        title: str,
        needed_in_days: int = 10,
    ) -> str:
        with Session(get_engine()) as db:
            site_id = db.scalars(select(Site.id).where(Site.name == site)).one()
        needed = (self.today() + timedelta(days=needed_in_days)).isoformat()
        bom = self.call(owner, "POST", "/boms", json={"site_id": str(site_id), "title": title, "client_ref": uuid.uuid4().hex,
                                                       "rows": [{"item": item, "quantity": qty, "unit": unit, "needed_by": needed}]})  # fmt: skip
        pub = self.call(owner, "POST", f"/boms/{bom['id']}/publish")
        rfq: str = pub["lines"][0]["rfq"]["id"]
        return rfq

    def send(self, owner: str, rfq: str) -> None:
        self.call(owner, "POST", f"/rfqs/{rfq}/send")
        for _ in range(60):  # invites wait for working hours
            if all(
                s["status"] != "queued"
                for s in self.call(owner, "GET", f"/rfqs/{rfq}")["shortlist"]
            ):
                return
            self.advance(30)
        raise DemoError("invites never went out")

    def close_bids(self) -> None:
        self.advance(24 * 60 + 5)

    def status(self, owner: str, rfq: str) -> str:
        s: str = self.call(owner, "GET", f"/rfqs/{rfq}")["status"]
        return s

    def approve(self, owner: str, rfq: str, choice: str = "l1") -> dict[str, Any]:
        v = self.call(owner, "GET", f"/rfqs/{rfq}")["version"]
        out: dict[str, Any] = self.call(
            owner,
            "POST",
            f"/rfqs/{rfq}/approve",
            json={"idempotency_key": uuid.uuid4().hex, "choice": choice, "version": v},
        )
        return out

    # --- vendor steps ------------------------------------------------------------------

    def quote(self, vendor: str, rfq: str, price: str, **extra: Any) -> None:
        today = self.today()
        body = {"client_message_id": uuid.uuid4().hex, "rfq_id": rfq, "unit_price": price, "price_unit": "bag",
                "gst_included": False, "gst_percent": "18", "delivery_date": (today + timedelta(days=5)).isoformat(),
                "validity_until": (today + timedelta(days=25)).isoformat(), "payment_terms_days": 15, **extra}  # fmt: skip
        self.call(vendor, "POST", "/vendor/quotes", json=body)

    def say(self, vendor: str, rfq: str, text: str = "", button: str | None = None) -> None:
        self.call(
            vendor,
            "POST",
            "/vendor/messages",
            json={
                "client_message_id": uuid.uuid4().hex,
                "rfq_id": rfq,
                "text": text or (button or ""),
                "button": button,
            },
        )

    def winner(self, wo: dict[str, Any]) -> str:
        """Phone of the vendor a work order went to."""
        with Session(get_engine()) as db:
            phone: str = db.scalars(
                select(Vendor.phone).where(Vendor.display_name == wo["vendor"])
            ).one()
        return phone

    def sample(self, vendor: str, rfq: str, name: str) -> None:
        self.call(
            vendor,
            "POST",
            f"/vendor/samples/{name}?client_message_id={uuid.uuid4().hex}&rfq_id={rfq}",
        )

    def everyone_holds(self, vendors: list[str], rfq: str) -> None:
        for v in vendors:
            self.say(v, rfq, "No discount possible")
        self.settle()


# --- scenarios ---------------------------------------------------------------------------


def happy_path(d: Driver) -> dict[str, Any]:
    """30 bags OPC 53 -> 5 matched -> quotes 400-500 -> 3 rounds -> L1 at ₹380, ready to approve."""
    rfq = d.publish(
        SHARMA,
        "Noida Sector 62 Tower",
        "OPC 53 Grade Cement",
        "30",
        "bag",
        "Scenario 1: Tower A slab",
    )
    d.send(SHARMA, rfq)
    for vendor, price in (
        (DELHI, "400"),
        (BALAJI, "420"),
        (GUPTA, "450"),
        (YADAV, "480"),
        (MAHALAXMI, "500"),
    ):
        d.quote(vendor, rfq, price)
    d.close_bids()
    # Round 1: the best vendor is asked for 3% off, the others to match ₹400.
    d.say(DELHI, rfq, "390 final")
    d.say(BALAJI, rfq, "405")
    d.say(GUPTA, rfq, "No discount possible")
    d.settle()
    # Round 2
    d.say(DELHI, rfq, "385")
    d.say(BALAJI, rfq, "392")
    d.settle()
    # Round 3: best and final
    d.say(DELHI, rfq, "380 final")
    d.say(BALAJI, rfq, "388 last rate")
    d.settle()
    return {"rfq": rfq, "status": d.status(SHARMA, rfq)}


def big_order(d: Driver) -> dict[str, Any]:
    """2,000 bags: no single vendor has the capacity -> split-award proposal."""
    rfq = d.publish(
        SHARMA,
        "Noida Sector 62 Tower",
        "OPC 53 Grade Cement",
        "2000",
        "bag",
        "Scenario 2: Podium raft (big order)",
    )
    d.send(SHARMA, rfq)
    quoted = [(BALAJI, "384"), (GUPTA, "382"), (YADAV, "390"), (MAHALAXMI, "379")]
    for vendor, price in quoted:
        d.quote(vendor, rfq, price)
    d.close_bids()
    d.everyone_holds([v for v, _ in quoted], rfq)
    return {"rfq": rfq, "status": d.status(SHARMA, rfq)}


def pdf_quotes(d: Driver) -> dict[str, Any]:
    """Quotes as PDFs and a photo, incl. an arithmetic error, a rate list and hidden instructions."""
    rfq = d.publish(
        ARORA,
        "Faridabad Sector 21",
        "OPC 53 Grade Cement",
        "30",
        "bag",
        "Scenario 3: PDF and photo quotes",
    )
    d.send(ARORA, rfq)
    for vendor, sample in (
        (MAHALAXMI, "clean"),
        (DELHI, "arithmetic"),
        (BALAJI, "ratelist"),
        (YADAV, "scanned"),
        (SINGH, "hidden"),
    ):
        d.sample(vendor, rfq, sample)
    d.settle()
    for vendor in (MAHALAXMI, BALAJI, YADAV):  # these vendors confirm what we read
        d.say(vendor, rfq, button="Yes")
    return {"rfq": rfq, "status": d.status(ARORA, rfq)}


def capacity_conflict(d: Driver) -> dict[str, Any]:
    """Two builders award Gupta (1,000 bags/week) 600 bags each for the same week."""
    a = d.publish(
        SHARMA,
        "Noida Sector 62 Tower",
        "OPC 53 Grade Cement",
        "600",
        "bag",
        "Scenario 4: Block C columns",
    )
    b = d.publish(
        GREENLINE,
        "Ghaziabad Raj Nagar Extension",
        "OPC 53 Grade Cement",
        "600",
        "bag",
        "Scenario 4: Tower 2 footing",
    )
    for owner, rfq in ((SHARMA, a), (GREENLINE, b)):
        d.send(owner, rfq)
    for rfq in (a, b):
        d.quote(GUPTA, rfq, "370")
        d.quote(BALAJI, rfq, "395")
    d.quote(YADAV, a, "398")
    d.close_bids()
    d.everyone_holds([GUPTA, BALAJI, YADAV], a)
    d.everyone_holds([GUPTA, BALAJI], b)
    d.approve(SHARMA, a)
    d.say(GUPTA, a, button="Confirm")  # Sharma's order is confirmed first
    return {"rfq": b, "blocked_rfq": b, "first_rfq": a, "status": d.status(GREENLINE, b)}


def handoff(d: Driver) -> dict[str, Any]:
    """A vendor asks for a phone call in the middle of negotiating."""
    rfq = d.publish(
        GREENLINE,
        "Dwarka Sector 19",
        "OPC 53 Grade Cement",
        "40",
        "bag",
        "Scenario 5: Dwarka lift core",
    )
    d.send(GREENLINE, rfq)
    for vendor, price in ((BALAJI, "392"), (MAHALAXMI, "386"), (SINGH, "399")):
        d.quote(vendor, rfq, price)
    d.close_bids()
    d.say(MAHALAXMI, rfq, "can you call me? easier to discuss on phone")
    d.say(BALAJI, rfq, "No discount possible")
    d.say(SINGH, rfq, "No discount possible")
    d.settle()
    return {"rfq": rfq, "status": d.status(GREENLINE, rfq)}


def timeout_runner_up(d: Driver) -> dict[str, Any]:
    """The winner never confirms the work order: it expires and the runner-up is offered."""
    rfq = d.publish(
        ARORA, "Faridabad Sector 21", "PPC Cement", "50", "bag", "Scenario 6: Boundary wall"
    )
    d.send(ARORA, rfq)
    for vendor, price in ((MAHALAXMI, "352"), (BALAJI, "360"), (YADAV, "366")):
        d.quote(vendor, rfq, price)
    d.close_bids()
    d.everyone_holds([MAHALAXMI, BALAJI, YADAV], rfq)
    d.approve(ARORA, rfq)
    d.advance(24 * 60)  # 4 working hours pass without a confirmation
    return {"rfq": rfq, "status": d.status(ARORA, rfq)}


def short_delivery(d: Driver) -> dict[str, Any]:
    """5 t of TMT: 4.8 t arrives and the invoice bills 5 t at a higher rate."""
    rfq = d.publish(
        SHARMA, "Gurugram Sector 49", "TMT Bar Fe 500D", "5", "tonne", "Scenario 7: Gurugram rebar"
    )
    d.send(SHARMA, rfq)
    for vendor, price in ((GOYAL, "57500"), (BANSAL, "58200")):
        d.quote(vendor, rfq, price, price_unit="tonne")
    d.close_bids()
    d.everyone_holds([GOYAL, BANSAL], rfq)
    wo = d.approve(SHARMA, rfq)["work_orders"][0]
    vendor = d.winner(wo)  # scores decide the winner, not the script
    d.say(vendor, rfq, button="Confirm")
    d.call(
        vendor,
        "POST",
        f"/vendor/work-orders/{wo['id']}/dispatch",
        json={"client_ref": uuid.uuid4().hex, "vehicle_no": "HR55AB7788"},
    )
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    d.as_(SHARMA_SE).post(f"/api/work-orders/{wo['id']}/receive?qty=4.8", content=png)
    # Billed for the full 5 t at ₹250/t above the agreed rate (+18% GST): two mismatches.
    rate = d.call(SHARMA, "GET", f"/work-orders/{wo['id']}")["unit_price_paise"] // 100 + 250
    d.as_(SHARMA).post(
        f"/api/work-orders/{wo['id']}/invoice?invoice_no=GST-2291&po_code={wo['code']}"
        f"&amount={rate * 5 * 118 // 100}&unit_price={rate}&client_ref={uuid.uuid4().hex}",
        content=b"",
    )
    return {"rfq": rfq, "work_order": wo["id"], "status": d.status(SHARMA, rfq)}


# Load order: scenarios whose demo point has no pending timers first; the one left
# mid-bidding last, so no later clock jump closes it early.
SCENARIOS: list[tuple[str, str, Callable[[Driver], dict[str, Any]]]] = [
    ("6", "Timeout and runner-up", timeout_runner_up),
    ("7", "Short delivery and invoice mismatch", short_delivery),
    ("4", "Capacity conflict", capacity_conflict),
    ("1", "Happy path", happy_path),
    ("2", "Big order split", big_order),
    ("5", "Handoff", handoff),
    ("3", "PDF quotes", pdf_quotes),
]


def play_full(d: Driver) -> dict[str, Any]:
    """Scenario 1 from start to a closed order, moving the demo clock as it goes."""
    out = happy_path(d)
    rfq = out["rfq"]
    wo = d.approve(SHARMA, rfq)["work_orders"][0]
    vendor = d.winner(wo)
    d.say(vendor, rfq, button="Confirm")
    d.advance(24 * 60)  # next day
    d.call(
        vendor,
        "POST",
        f"/vendor/work-orders/{wo['id']}/dispatch",
        json={"client_ref": uuid.uuid4().hex, "vehicle_no": "DL1LX2345"},
    )
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    d.as_(SHARMA_SE).post(f"/api/work-orders/{wo['id']}/receive?qty=30", content=png)
    detail = d.call(SHARMA, "GET", f"/work-orders/{wo['id']}")
    total = f"{detail['total_paise'] / 100:.2f}"
    unit = f"{detail['unit_price_paise'] / 100:.2f}"
    d.as_(SHARMA).post(
        f"/api/work-orders/{wo['id']}/invoice?invoice_no=INV-1001&po_code={wo['code']}&amount={total}&unit_price={unit}&client_ref={uuid.uuid4().hex}",
        content=b"",
    )
    d.call(SHARMA, "POST", f"/work-orders/{wo['id']}/close", json={})
    return {"rfq": rfq, "work_order": wo["id"], "status": d.status(SHARMA, rfq)}
