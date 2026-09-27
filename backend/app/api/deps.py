"""Shared request dependencies: clocks, the signed-in principal, role guards."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.permissions import BUILDER_ROLES, can
from app.config import get_settings
from app.db.models import AuthSession, User, Vendor
from app.db.session import get_db
from app.jobs.clock import Clock, SystemClock
from app.jobs.demo_clock import load_clock

COOKIE = "zp_session"
SESSION_TTL = timedelta(hours=12)

Db = Annotated[Session, Depends(get_db)]


def get_wall_clock() -> Clock:
    """Real time. Auth (OTP and session expiry) always uses this, never the demo clock."""
    return SystemClock()


def get_clock(db: Db) -> Clock:
    """Business time: the demo clock in demo mode."""
    return load_clock(db)


WallClock = Annotated[Clock, Depends(get_wall_clock)]
BizClock = Annotated[Clock, Depends(get_clock)]


def issue_token(db: Session, kind: str, subject_id: uuid.UUID, clock: Clock) -> str:
    now = clock.now()
    s = AuthSession(subject_kind=kind, subject_id=subject_id, expires_at=now + SESSION_TTL)
    db.add(s)
    db.commit()
    claims = {"sid": str(s.id), "sub": str(subject_id), "kind": kind, "iat": int(now.timestamp())}
    return jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")


@dataclass
class Principal:
    session_id: uuid.UUID
    user: User | None = None
    vendor: Vendor | None = None


def current_principal(request: Request, db: Db, clock: WallClock) -> Principal:
    unauth = HTTPException(401, "Not signed in")
    token = request.cookies.get(COOKIE)
    if not token:
        raise unauth
    try:
        # Expiry lives on the session row (checked below against the injected clock).
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        sid = uuid.UUID(claims["sid"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauth from None
    s = db.get(AuthSession, sid)
    if (
        s is None
        or s.revoked_at is not None
        or s.expires_at <= clock.now()
        or str(s.subject_id) != claims.get("sub")
    ):
        raise unauth
    if s.subject_kind == "user":
        user = db.get(User, s.subject_id)
        if user is None or not user.is_active:
            raise unauth
        return Principal(session_id=sid, user=user)
    vendor = db.get(Vendor, s.subject_id)
    if vendor is None:
        raise unauth
    return Principal(session_id=sid, vendor=vendor)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def current_builder(p: CurrentPrincipal) -> User:
    if p.user is None or p.user.role not in BUILDER_ROLES or p.user.builder_org_id is None:
        raise HTTPException(403, "Builder account required")
    return p.user


def current_vendor(p: CurrentPrincipal) -> Vendor:
    if p.vendor is None:
        raise HTTPException(403, "Vendor account required")
    return p.vendor


def current_admin(p: CurrentPrincipal) -> User:
    if p.user is None or p.user.role != "admin":
        raise HTTPException(403, "Admin account required")
    return p.user


Builder = Annotated[User, Depends(current_builder)]
VendorUser = Annotated[Vendor, Depends(current_vendor)]
Admin = Annotated[User, Depends(current_admin)]


def require(permission: str) -> Callable[[User], User]:
    def dep(user: Builder) -> User:
        if not can(user.role, permission):
            raise HTTPException(403, "Your role cannot do this")
        return user

    return dep


def org_id(user: User) -> uuid.UUID:
    assert user.builder_org_id is not None
    return user.builder_org_id
