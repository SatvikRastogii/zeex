"""BOM intake: templates, validation, create, publish, revise, cancel."""

import io
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.match_runner import run_matching
from app.agents.outreach import schedule_rfq_update
from app.api.deps import BizClock, Builder, Db, org_id, require
from app.db.audit import audit
from app.db.catalog import build_index
from app.db.ids import next_code, rfq_code
from app.db.models import Bom, BomLine, BuilderOrg, CatalogItem, Rfq, Site, User
from app.db.tenancy import get_owned
from app.domain.bom_rows import (
    MAX_FILE_BYTES,
    FileError,
    RowResult,
    read_table,
    template_csv,
    validate_row,
    validate_rows,
)
from app.domain.settings import org_settings
from app.domain.states import InvalidTransition, transition
from app.domain.units import format_qty
from app.files import get_file_store
from app.jobs.clock import Clock, ist_today

router = APIRouter(prefix="/boms", tags=["boms"])
catalog_router = APIRouter(tags=["catalog"])

Creator = Annotated[User, Depends(require("bom.create"))]

EDITABLE_LINE_STATES = {"validated", "published", "in_progress"}
LOCKED_RFQ_STATES = {"awaiting_approval", "awarded", "closed", "cancelled"}


def _site_context(db: Session, org: uuid.UUID, site_id: uuid.UUID) -> tuple[Site, set[str]]:
    site = get_owned(db, Site, site_id, org)
    others = {s.name.lower() for s in db.scalars(select(Site).where(Site.builder_org_id == org))}
    others.discard(site.name.lower())
    return site, others


def _validate(
    db: Session, org: uuid.UUID, site_id: uuid.UUID, raws: list[dict[str, Any]], clock: Clock
) -> list[RowResult]:
    site, others = _site_context(db, org, site_id)
    try:
        return validate_rows(
            raws,
            build_index(db),
            today=ist_today(clock),
            site_name=site.name,
            other_site_names=others,
        )
    except FileError as e:
        raise HTTPException(422, str(e)) from None


def _result(rows: list[RowResult], file_ref: str | None = None) -> dict[str, Any]:
    return {
        "file_ref": file_ref,
        "rows": [r.as_dict() for r in rows],
        "ok": all(not r.errors for r in rows),
        "error_count": sum(len(r.errors) for r in rows),
    }


@catalog_router.get("/catalog")
def list_catalog(_: Builder, db: Db) -> list[dict[str, Any]]:
    return [
        {
            "id": str(c.id),
            "code": c.code,
            "category": c.category,
            "name": c.name,
            "grade": c.grade,
            "canonical_unit": c.canonical_unit,
            "aliases": c.aliases,
        }
        for c in db.scalars(select(CatalogItem).order_by(CatalogItem.category, CatalogItem.name))
    ]


# --- templates -----------------------------------------------------------------------------


@router.get("/template.csv")
def template_as_csv(_: Builder) -> Response:
    return Response(
        template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="bom-template.csv"'},
    )


@router.get("/template.xlsx")
def template_as_xlsx(_: Builder) -> Response:
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    for line in template_csv().strip().splitlines():
        ws.append(line.split(","))
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="bom-template.xlsx"'},
    )


# --- validate ------------------------------------------------------------------------------


@router.post("/validate-file")
async def validate_file(
    request: Request, site_id: uuid.UUID, filename: str, user: Creator, db: Db, clock: BizClock
) -> dict[str, Any]:
    """Raw file bytes in the request body (no multipart needed)."""
    data = await request.body()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "The file is larger than 5 MB.")
    try:
        raws = read_table(data, filename)
    except FileError as e:
        raise HTTPException(422, str(e)) from None
    rows = _validate(db, org_id(user), site_id, raws, clock)
    ref = get_file_store().save(f"boms/{org_id(user)}", filename, data)
    return _result(rows, ref)


class RowIn(BaseModel):
    item: str = ""
    spec: str = ""
    quantity: str = ""
    unit: str = ""
    needed_by: str = ""
    site: str = ""
    partial_allowed: str = ""
    notes: str = ""
    catalog_item_id: uuid.UUID | None = None


class ValidateIn(BaseModel):
    site_id: uuid.UUID
    rows: list[RowIn]


@router.post("/validate")
def validate_json(body: ValidateIn, user: Creator, db: Db, clock: BizClock) -> dict[str, Any]:
    raws = [r.model_dump() for r in body.rows]
    return _result(_validate(db, org_id(user), body.site_id, raws, clock))


# --- create / read -------------------------------------------------------------------------


class BomIn(ValidateIn):
    title: str | None = Field(default=None, max_length=200)
    source: str = Field(default="manual", pattern="^(upload|manual)$")
    file_ref: str | None = None
    client_ref: str = Field(min_length=8, max_length=64)


@router.post("", status_code=201)
def create_bom(
    body: BomIn, response: Response, user: Creator, db: Db, clock: BizClock
) -> dict[str, Any]:
    org = org_id(user)
    existing = db.scalars(select(Bom).where(Bom.client_ref == body.client_ref)).one_or_none()
    if existing is not None:
        if existing.builder_org_id != org:
            raise HTTPException(409, "Duplicate request reference")
        response.status_code = 200  # replay of the same create
        return bom_detail(db, existing)
    if body.file_ref and not body.file_ref.startswith(f"boms/{org}/"):
        raise HTTPException(422, "Unknown file reference")

    rows = _validate(db, org, body.site_id, [r.model_dump() for r in body.rows], clock)
    result = _result(rows)
    if not result["ok"]:
        raise HTTPException(422, {"message": "Fix the highlighted rows first.", **result})

    bom = Bom(
        builder_org_id=org,
        public_code=next_code(db, "bom", clock),
        site_id=body.site_id,
        created_by=user.id,
        status="draft",
        source=body.source,
        original_file_ref=body.file_ref,
        title=body.title,
        client_ref=body.client_ref,
    )
    db.add(bom)
    db.flush()
    line_no = 0
    for r in rows:
        if r.merged_into is not None:
            continue
        assert r.item and r.qty_canonical_milli and r.needed_by
        line_no += 1
        db.add(
            BomLine(
                builder_org_id=org,
                bom_id=bom.id,
                line_no=line_no,
                catalog_item_id=r.item.id,
                raw_text=r.input["item"] or r.item.name,
                spec=r.input["spec"] or None,
                notes=r.input["notes"] or None,
                qty_canonical_milli=r.qty_canonical_milli,
                unit=r.input["unit"],
                qty_entered=r.input["quantity"],
                needed_by=r.needed_by,
                partial_allowed=r.partial_allowed,
                status="open",
            )
        )
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action="bom.create",
        entity="bom",
        entity_id=bom.id,
        org_id=org,
        after={"code": bom.public_code, "lines": line_no},
    )
    transition(db, clock, "bom", bom, "validated", actor=f"user:{user.id}")
    db.commit()
    return bom_detail(db, bom)


def bom_detail(db: Session, bom: Bom) -> dict[str, Any]:
    site = db.get(Site, bom.site_id)
    assert site is not None
    lines = db.scalars(select(BomLine).where(BomLine.bom_id == bom.id).order_by(BomLine.line_no))
    rfqs = {
        r.bom_line_id: r
        for r in db.scalars(
            select(Rfq).join(BomLine, Rfq.bom_line_id == BomLine.id).where(BomLine.bom_id == bom.id)
        )
    }
    items = {c.id: c for c in db.scalars(select(CatalogItem))}
    out_lines = []
    for ln in lines:
        item = items.get(ln.catalog_item_id) if ln.catalog_item_id else None
        rfq = rfqs.get(ln.id)
        out_lines.append(
            {
                "id": str(ln.id),
                "line_no": ln.line_no,
                "item": {"id": str(item.id), "code": item.code, "name": item.name}
                if item
                else None,
                "raw_text": ln.raw_text,
                "spec": ln.spec,
                "qty_canonical_milli": ln.qty_canonical_milli,
                "qty_display": format_qty(
                    ln.qty_canonical_milli, item.canonical_unit if item else ln.unit
                ),
                "entered": f"{ln.qty_entered} {ln.unit}",
                "needed_by": ln.needed_by.isoformat(),
                "partial_allowed": ln.partial_allowed,
                "status": ln.status,
                "rfq": {
                    "id": str(rfq.id),
                    "code": rfq.public_code,
                    "status": rfq.status,
                    "stale": rfq.stale,
                    "revision": rfq.revision,
                }
                if rfq
                else None,
            }
        )
    return {
        "id": str(bom.id),
        "code": bom.public_code,
        "title": bom.title,
        "status": bom.status,
        "revision": bom.revision,
        "source": bom.source,
        "has_file": bom.original_file_ref is not None,
        "site": {"id": str(site.id), "name": site.name, "area": site.area},
        "created_at": bom.created_at,
        "lines": out_lines,
    }


@router.get("")
def list_boms(user: Builder, db: Db) -> list[dict[str, Any]]:
    org = org_id(user)
    counts: dict[uuid.UUID, int] = {
        bom_id: n
        for bom_id, n in db.execute(
            select(BomLine.bom_id, func.count())
            .where(BomLine.builder_org_id == org)
            .group_by(BomLine.bom_id)
        )
    }
    sites = {s.id: s.name for s in db.scalars(select(Site).where(Site.builder_org_id == org))}
    boms = db.scalars(select(Bom).where(Bom.builder_org_id == org).order_by(Bom.created_at.desc()))
    return [
        {
            "id": str(b.id),
            "code": b.public_code,
            "title": b.title,
            "status": b.status,
            "revision": b.revision,
            "site": sites.get(b.site_id),
            "lines": counts.get(b.id, 0),
            "created_at": b.created_at,
        }
        for b in boms
    ]


@router.get("/{bom_id}")
def get_bom(bom_id: uuid.UUID, user: Builder, db: Db) -> dict[str, Any]:
    return bom_detail(db, get_owned(db, Bom, bom_id, org_id(user)))


@router.get("/{bom_id}/file")
def get_bom_file(bom_id: uuid.UUID, user: Builder, db: Db) -> Response:
    bom = get_owned(db, Bom, bom_id, org_id(user))
    if not bom.original_file_ref:
        raise HTTPException(404, "Not found")
    name = f"{bom.public_code}{bom.original_file_ref[bom.original_file_ref.rfind('.') :]}"
    return Response(
        get_file_store().read(bom.original_file_ref),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --- publish / revise / cancel -------------------------------------------------------------


@router.post("/{bom_id}/publish")
def publish(bom_id: uuid.UUID, user: Creator, db: Db, clock: BizClock) -> dict[str, Any]:
    bom = get_owned(db, Bom, bom_id, org_id(user))
    if bom.status == "published":
        return bom_detail(db, bom)  # idempotent
    try:
        transition(db, clock, "bom", bom, "published", actor=f"user:{user.id}")
    except InvalidTransition:
        raise HTTPException(409, f"A {bom.status} BOM cannot be published") from None
    org = db.get(BuilderOrg, bom.builder_org_id)
    assert org is not None
    cfg = org_settings(org.settings)
    rfqs = []
    for ln in db.scalars(select(BomLine).where(BomLine.bom_id == bom.id).order_by(BomLine.line_no)):
        rfqs.append(
            Rfq(
                builder_org_id=bom.builder_org_id,
                bom_line_id=ln.id,
                public_code=rfq_code(bom.public_code, ln.line_no),
                status="draft",
                max_rounds=cfg["max_rounds"],
                shortlist_size=cfg["shortlist_size"],
            )
        )
    db.add_all(rfqs)
    db.flush()
    for rfq in rfqs:  # match vendors for every line straight away
        run_matching(db, clock, rfq, actor=f"user:{user.id}")
    db.commit()
    return bom_detail(db, bom)


class LineEdit(BaseModel):
    quantity: str | None = None
    unit: str | None = None
    needed_by: str | None = None
    partial_allowed: bool | None = None


@router.patch("/{bom_id}/lines/{line_id}")
def edit_line(
    bom_id: uuid.UUID, line_id: uuid.UUID, body: LineEdit, user: Creator, db: Db, clock: BizClock
) -> dict[str, Any]:
    """Editing a published BOM bumps its revision and marks the line's RFQ stale so
    invited vendors are re-sent the change."""
    org = org_id(user)
    bom = get_owned(db, Bom, bom_id, org)
    line = get_owned(db, BomLine, line_id, org)
    if line.bom_id != bom.id:
        raise HTTPException(404, "Not found")
    if bom.status not in EDITABLE_LINE_STATES:
        raise HTTPException(409, f"Lines of a {bom.status} BOM cannot be edited")
    rfq = db.scalars(select(Rfq).where(Rfq.bom_line_id == line.id)).one_or_none()
    if rfq is not None and rfq.status in LOCKED_RFQ_STATES:
        raise HTTPException(409, f"{rfq.public_code} is {rfq.status}; it can no longer change")

    site, others = _site_context(db, org, bom.site_id)
    raw: dict[str, Any] = {
        "item": line.raw_text,
        "spec": line.spec or "",
        "catalog_item_id": line.catalog_item_id,
        "quantity": body.quantity if body.quantity is not None else line.qty_entered,
        "unit": body.unit if body.unit is not None else line.unit,
        "needed_by": body.needed_by if body.needed_by is not None else line.needed_by.isoformat(),
        "partial_allowed": (
            "yes"
            if (body.partial_allowed if body.partial_allowed is not None else line.partial_allowed)
            else "no"
        ),
    }
    r = validate_row(
        line.line_no,
        raw,
        build_index(db),
        today=ist_today(clock),
        site_name=site.name,
        other_site_names=others,
    )
    if r.errors:
        raise HTTPException(422, {"message": "Fix the highlighted fields.", "rows": [r.as_dict()]})
    assert r.qty_canonical_milli and r.needed_by
    before = {
        "qty_milli": line.qty_canonical_milli,
        "needed_by": line.needed_by.isoformat(),
        "partial_allowed": line.partial_allowed,
    }
    line.qty_canonical_milli, line.needed_by = r.qty_canonical_milli, r.needed_by
    line.partial_allowed = r.partial_allowed
    line.qty_entered, line.unit = raw["quantity"], raw["unit"]
    after = {
        "qty_milli": line.qty_canonical_milli,
        "needed_by": line.needed_by.isoformat(),
        "partial_allowed": line.partial_allowed,
    }
    if before == after:
        return bom_detail(db, bom)
    if bom.status != "validated":
        bom.revision += 1
        if rfq is not None:
            rfq.revision += 1
            rfq.stale = rfq.status not in {"draft", "matching", "no_vendors_matched"}
            if rfq.status in {"invited", "bidding"}:
                schedule_rfq_update(db, clock, rfq)  # vendors get the change
    audit(
        db,
        clock,
        actor=f"user:{user.id}",
        action="bom_line.revise",
        entity="bom_line",
        entity_id=line.id,
        org_id=org,
        before=before,
        after={**after, "bom_revision": bom.revision},
    )
    db.commit()
    return bom_detail(db, bom)


@router.post("/{bom_id}/cancel")
def cancel(bom_id: uuid.UUID, user: Creator, db: Db, clock: BizClock) -> dict[str, Any]:
    bom = get_owned(db, Bom, bom_id, org_id(user))
    if bom.status == "cancelled":
        return bom_detail(db, bom)
    try:
        transition(db, clock, "bom", bom, "cancelled", actor=f"user:{user.id}")
    except InvalidTransition:
        raise HTTPException(409, f"A {bom.status} BOM cannot be cancelled") from None
    for rfq in db.scalars(
        select(Rfq).join(BomLine, Rfq.bom_line_id == BomLine.id).where(BomLine.bom_id == bom.id)
    ):
        if rfq.status not in {"cancelled", "closed", "failed"}:
            transition(
                db, clock, "rfq", rfq, "cancelled", actor=f"user:{user.id}", reason="BOM cancelled"
            )
    db.commit()
    return bom_detail(db, bom)
