"""Phone OTP: 6 digits, 5-minute expiry, single use, 5 attempts per challenge,
5 challenges per phone per 15 minutes, stored as argon2 hashes."""

import secrets
import uuid
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import OtpChallenge, User, Vendor

OTP_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_CHALLENGES = 5
RATE_WINDOW = timedelta(minutes=15)

_hasher = PasswordHasher()


class OtpError(Exception):
    """Always shown to the user as 'Invalid or expired code'."""


class RateLimited(Exception):
    pass


def find_subject(db: Session, phone: str) -> tuple[str, uuid.UUID] | None:
    user = db.scalars(select(User).where(User.phone == phone, User.is_active)).one_or_none()
    if user:
        return "user", user.id
    vendor = db.scalars(select(Vendor).where(Vendor.phone == phone)).one_or_none()
    if vendor:
        return "vendor", vendor.id
    return None


def request_otp(db: Session, phone: str, now: datetime) -> str | None:
    """Create a challenge and return the code, or None for an unknown phone.
    Callers answer both cases identically so phone numbers can't be probed."""
    # issued_at = expires_at - TTL; count challenges issued inside the window
    recent = db.scalar(
        select(func.count())
        .select_from(OtpChallenge)
        .where(OtpChallenge.phone == phone, OtpChallenge.expires_at > now - RATE_WINDOW + OTP_TTL)
    )
    if (recent or 0) >= MAX_CHALLENGES:
        raise RateLimited
    if find_subject(db, phone) is None:
        return None
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(OtpChallenge(phone=phone, code_hash=_hasher.hash(code), expires_at=now + OTP_TTL))
    db.commit()
    return code


def verify_otp(db: Session, phone: str, code: str, now: datetime) -> tuple[str, uuid.UUID]:
    """Check the latest challenge for this phone. Older challenges are dead."""
    ch = db.scalars(
        select(OtpChallenge)
        .where(OtpChallenge.phone == phone)
        .order_by(OtpChallenge.created_at.desc())  # most recently issued
        .limit(1)
        .with_for_update()
    ).one_or_none()
    if ch is None or ch.consumed_at or ch.expires_at <= now or ch.attempts >= MAX_ATTEMPTS:
        db.rollback()
        raise OtpError
    ch.attempts += 1
    try:
        ok: bool = _hasher.verify(ch.code_hash, code)
    except VerificationError:
        ok = False
    if not ok:
        db.commit()
        raise OtpError
    subject = find_subject(db, phone)
    if subject is None:
        db.commit()
        raise OtpError
    ch.consumed_at = now
    db.commit()
    return subject
