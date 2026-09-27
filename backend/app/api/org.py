import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from app.api.deps import BizClock, Builder, Db, org_id, require
from app.db.audit import audit
from app.db.models import BuilderOrg, Site, User
from app.db.tenancy import get_owned
from app.domain.phone import normalize_phone
from app.domain.settings import DEFAULT_WEIGHTS, org_settings

router = APIRouter(tags=["org"])

SettingsEditor = Annotated[User, Depends(require("settings.edit"))]
UserManager = Annotated[User, Depends(require("users.manage"))]

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
Role = Literal["owner", "purchase_manager", "site_engineer"]


class WorkingHours(BaseModel):
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)

    @model_validator(mode="after")
    def _order(self) -> "WorkingHours":
        if self.start >= self.end:
            raise ValueError("working hours must end after they start")
        return self


class SettingsIn(BaseModel):
    weights: dict[str, int]
    gst_mode: Literal["incl", "excl"]
    disclosure: Literal["off", "lower_offer_only", "lower_offer_then_price"]
    approval_limits_paise: dict[Literal["purchase_manager"], Annotated[int, Field(ge=0)]]
    working_hours: WorkingHours
    bid_window_hours: int = Field(ge=1, le=168)
    max_rounds: int = Field(ge=1, le=5)
    shortlist_size: int = Field(ge=1, le=5)
    match_top_n: int = Field(ge=1, le=15)
    po_confirm_working_hours: int = Field(ge=1, le=48)
    reply_timeout_working_hours: int = Field(ge=1, le=24)

    @field_validator("weights")
    @classmethod
    def _weights(cls, w: dict[str, int]) -> dict[str, int]:
        if set(w) != set(DEFAULT_WEIGHTS):
            raise ValueError(f"weights must have exactly: {', '.join(DEFAULT_WEIGHTS)}")
        if any(v < 0 or v > 100 for v in w.values()):
            raise ValueError("each weight must be between 0 and 100")
        if sum(w.values()) != 100:
            raise ValueError(f"weights must sum to 100 (got {sum(w.values())})")
        return w


def _user_out(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "phone": u.phone,
        "name": u.name,
        "role": u.role,
        "approval_limit_paise": u.approval_limit_paise,
        "is_active": u.is_active,
    }


@router.get("/org")
def get_org(user: Builder, db: Db) -> dict[str, Any]:
    org = db.get(BuilderOrg, org_id(user))
    assert org is not None
    return {
        "id": str(org.id),
        "name": org.name,
        "gstin": org.gstin,
        "settings": org_settings(org.settings),
    }


@router.put("/org/settings")
def put_settings(body: SettingsIn, user: SettingsEditor, db: Db, clock: BizClock) -> dict[str, Any]:
    org = db.get(BuilderOrg, org_id(user))
    assert org is not None
    before = org_settings(org.settings)
    org.settings = {**before, **body.model_dump()}
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action="settings.update",
        entity="builder_org",
        entity_id=org.id,
        org_id=org.id,
        before=before,
        after=org.settings,
    )
    db.commit()
    return {"settings": org.settings}


@router.get("/org/users")
def list_users(user: UserManager, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(select(User).where(User.builder_org_id == org_id(user)).order_by(User.name))
    return [_user_out(u) for u in rows]


class UserIn(BaseModel):
    phone: str
    name: str = Field(min_length=1, max_length=100)
    role: Role
    approval_limit_paise: int | None = Field(default=None, ge=0)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


@router.post("/org/users", status_code=201)
def create_user(body: UserIn, user: UserManager, db: Db, clock: BizClock) -> dict[str, Any]:
    if db.scalars(select(User).where(User.phone == body.phone)).first():
        raise HTTPException(409, "Phone already registered")
    new = User(builder_org_id=org_id(user), **body.model_dump())
    db.add(new)
    db.flush()
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action="user.create",
        entity="user",
        entity_id=new.id,
        org_id=org_id(user),
        after=_user_out(new),
    )
    db.commit()
    return _user_out(new)


class UserPatch(BaseModel):
    role: Role | None = None
    approval_limit_paise: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


@router.patch("/org/users/{user_id}")
def patch_user(
    user_id: uuid.UUID, body: UserPatch, user: UserManager, db: Db, clock: BizClock
) -> dict[str, Any]:
    target = get_owned(db, User, user_id, org_id(user))
    changes = body.model_dump(exclude_unset=True)
    demoting = changes.get("role", "owner") != "owner"
    if target.id == user.id and (changes.get("is_active") is False or demoting):
        raise HTTPException(400, "You cannot deactivate or demote yourself")
    before = _user_out(target)
    for k, v in changes.items():
        setattr(target, k, v)
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action="user.update",
        entity="user",
        entity_id=target.id,
        org_id=org_id(user),
        before=before,
        after=_user_out(target),
    )
    db.commit()
    return _user_out(target)


def _site_out(s: Site) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "address": s.address,
        "area": s.area,
        "pincode": s.pincode,
    }


@router.get("/sites")
def list_sites(user: Builder, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(select(Site).where(Site.builder_org_id == org_id(user)).order_by(Site.name))
    return [_site_out(s) for s in rows]


@router.get("/sites/{site_id}")
def get_site(site_id: uuid.UUID, user: Builder, db: Db) -> dict[str, Any]:
    return _site_out(get_owned(db, Site, site_id, org_id(user)))
