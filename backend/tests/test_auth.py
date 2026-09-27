from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import COOKIE
from app.db.models import OtpChallenge, User
from app.jobs.clock import FixedClock
from app.main import app
from tests.phones import SHARMA_OWNER, SHARMA_PM, UNKNOWN, VENDOR_BALAJI

Login = Callable[[str], TestClient]
GENERIC = "Invalid or expired code"


def request_code(c: TestClient, phone: str) -> str:
    r = c.post("/api/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    code: str = r.json()["demo_otp"]
    return code


def verify(c: TestClient, phone: str, code: str) -> int:
    status: int = c.post("/api/auth/otp/verify", json={"phone": phone, "code": code}).status_code
    return status


@pytest.fixture
def c(seeded: Session, wall: FixedClock) -> TestClient:
    return TestClient(app)


def test_login_sets_httponly_cookie_and_me_works(c: TestClient) -> None:
    code = request_code(c, SHARMA_OWNER)
    r = c.post("/api/auth/otp/verify", json={"phone": SHARMA_OWNER, "code": code})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = c.get("/api/auth/me").json()
    assert me["role"] == "owner" and me["org"]["name"] == "Sharma Constructions"


def test_phone_formats_normalized(c: TestClient) -> None:
    code = request_code(c, "90000 10001")
    assert verify(c, "+91 9000010001", code) == 200


def test_invalid_phone_rejected(c: TestClient) -> None:
    assert c.post("/api/auth/otp/request", json={"phone": "12ab"}).status_code == 422


def test_otp_stored_hashed(c: TestClient, seeded: Session) -> None:
    code = request_code(c, SHARMA_OWNER)
    stored = seeded.scalars(select(OtpChallenge.code_hash)).one()
    assert code not in stored and stored.startswith("$argon2")


def test_unknown_phone_looks_the_same_and_cannot_verify(c: TestClient) -> None:
    r = c.post("/api/auth/otp/request", json={"phone": UNKNOWN})
    assert r.status_code == 200 and r.json() == {"sent": True}
    r = c.post("/api/auth/otp/verify", json={"phone": UNKNOWN, "code": "123456"})
    assert r.status_code == 400 and r.json()["detail"] == GENERIC


def test_wrong_code_generic_error(c: TestClient) -> None:
    code = request_code(c, SHARMA_OWNER)
    wrong = "000000" if code != "000000" else "111111"
    r = c.post("/api/auth/otp/verify", json={"phone": SHARMA_OWNER, "code": wrong})
    assert r.status_code == 400 and r.json()["detail"] == GENERIC


def test_otp_expires_after_5_minutes(c: TestClient, wall: FixedClock) -> None:
    code = request_code(c, SHARMA_OWNER)
    wall.advance(timedelta(minutes=5))
    assert verify(c, SHARMA_OWNER, code) == 400


def test_otp_valid_just_before_expiry(c: TestClient, wall: FixedClock) -> None:
    code = request_code(c, SHARMA_OWNER)
    wall.advance(timedelta(minutes=4, seconds=59))
    assert verify(c, SHARMA_OWNER, code) == 200


def test_otp_single_use(c: TestClient) -> None:
    code = request_code(c, SHARMA_OWNER)
    assert verify(c, SHARMA_OWNER, code) == 200
    assert verify(c, SHARMA_OWNER, code) == 400


def test_five_wrong_attempts_lock_the_challenge(c: TestClient) -> None:
    code = request_code(c, SHARMA_OWNER)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert verify(c, SHARMA_OWNER, wrong) == 400
    assert verify(c, SHARMA_OWNER, code) == 400  # right code, but locked


def test_new_code_invalidates_the_old_one(c: TestClient) -> None:
    old = request_code(c, SHARMA_OWNER)
    new = request_code(c, SHARMA_OWNER)
    if old != new:
        assert verify(c, SHARMA_OWNER, old) == 400
    assert verify(c, SHARMA_OWNER, new) == 200


def test_rate_limit_5_challenges_per_15_minutes(c: TestClient, wall: FixedClock) -> None:
    for _ in range(5):
        request_code(c, SHARMA_OWNER)
        wall.advance(timedelta(minutes=1))
    assert c.post("/api/auth/otp/request", json={"phone": SHARMA_OWNER}).status_code == 429
    wall.advance(timedelta(minutes=11))  # first challenge now older than 15 min
    assert c.post("/api/auth/otp/request", json={"phone": SHARMA_OWNER}).status_code == 200


def test_rate_limit_is_per_phone(c: TestClient) -> None:
    for _ in range(5):
        request_code(c, SHARMA_OWNER)
    assert c.post("/api/auth/otp/request", json={"phone": SHARMA_PM}).status_code == 200


def test_logout_invalidates_session_server_side(login: Login) -> None:
    c = login(SHARMA_OWNER)
    token = c.cookies[COOKIE]
    assert c.post("/api/auth/logout").status_code == 200
    replay = TestClient(app, cookies={COOKIE: token})
    assert replay.get("/api/auth/me").status_code == 401


def test_session_expires_after_12_hours(login: Login, wall: FixedClock) -> None:
    c = login(SHARMA_OWNER)
    wall.advance(timedelta(hours=11, minutes=59))
    assert c.get("/api/auth/me").status_code == 200
    wall.advance(timedelta(minutes=1))
    assert c.get("/api/auth/me").status_code == 401


def test_tampered_token_rejected(login: Login) -> None:
    c = login(SHARMA_OWNER)
    token = c.cookies[COOKIE]
    bad = TestClient(app, cookies={COOKIE: token[:-2] + ("AA" if token[-2:] != "AA" else "BB")})
    assert bad.get("/api/auth/me").status_code == 401


def test_no_cookie_is_401(c: TestClient) -> None:
    assert c.get("/api/auth/me").status_code == 401


def test_deactivated_user_loses_session_and_cannot_request(login: Login, seeded: Session) -> None:
    c = login(SHARMA_PM)
    user = seeded.scalars(select(User).where(User.phone == SHARMA_PM)).one()
    user.is_active = False
    seeded.commit()
    assert c.get("/api/auth/me").status_code == 401
    assert "demo_otp" not in c.post("/api/auth/otp/request", json={"phone": SHARMA_PM}).json()


def test_vendor_login(login: Login) -> None:
    me = login(VENDOR_BALAJI).get("/api/auth/me").json()
    assert me["kind"] == "vendor" and me["name"] == "Shree Balaji Cement Traders"
