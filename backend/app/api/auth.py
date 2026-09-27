import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, field_validator

from app.api.deps import COOKIE, SESSION_TTL, CurrentPrincipal, Db, WallClock, issue_token
from app.auth.otp import OtpError, RateLimited, request_otp, verify_otp
from app.config import get_settings
from app.db.models import AuthSession, BuilderOrg
from app.domain.phone import normalize_phone

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("auth")

GENERIC = "Invalid or expired code"


class PhoneIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class VerifyIn(PhoneIn):
    code: str


@router.post("/otp/request")
def otp_request(body: PhoneIn, db: Db, clock: WallClock) -> dict[str, Any]:
    try:
        code = request_otp(db, body.phone, clock.now())
    except RateLimited:
        raise HTTPException(429, "Too many codes requested. Try again in 15 minutes.") from None
    out: dict[str, Any] = {"sent": True}
    if code and get_settings().demo_mode:
        # Real mode would send a WhatsApp authentication template instead.
        log.info("DEMO OTP for %s: %s", body.phone, code)
        out["demo_otp"] = code
    return out


@router.post("/otp/verify")
def otp_verify(body: VerifyIn, response: Response, db: Db, clock: WallClock) -> dict[str, str]:
    code = body.code.strip()
    try:
        kind, subject_id = verify_otp(db, body.phone, code, clock.now())
    except OtpError:
        raise HTTPException(400, GENERIC) from None
    token = issue_token(db, kind, subject_id, clock)
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        path="/",
    )
    return {"kind": kind}


@router.post("/logout")
def logout(p: CurrentPrincipal, response: Response, db: Db, clock: WallClock) -> dict[str, bool]:
    s = db.get(AuthSession, p.session_id)
    if s is not None:
        s.revoked_at = clock.now()
        db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(p: CurrentPrincipal, db: Db) -> dict[str, Any]:
    if p.vendor is not None:
        return {"kind": "vendor", "id": str(p.vendor.id), "name": p.vendor.display_name}
    assert p.user is not None
    org = db.get(BuilderOrg, p.user.builder_org_id) if p.user.builder_org_id else None
    return {
        "kind": "user",
        "id": str(p.user.id),
        "name": p.user.name,
        "role": p.user.role,
        "org": {"id": str(org.id), "name": org.name} if org else None,
    }
