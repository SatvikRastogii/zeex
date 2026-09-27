"""Dispatch (vendor), receipt and invoices (builder), closure (Stage 11)."""

import uuid
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.delivery import (
    DeliveryError,
    InvoiceIn,
    check_invoice,
    close,
    dispatch,
    receive,
    received_milli,
)
from app.agents.quote_parser import MAX_FILE_BYTES, DocumentError, sniff
from app.api.approvals import _wo_out
from app.api.deps import BizClock, Db, VendorUser, org_id, require
from app.db.audit import audit
from app.db.models import Delivery, Invoice, User, WorkOrder
from app.db.tenancy import get_owned
from app.domain.money import rupees_to_paise
from app.files import get_file_store

router = APIRouter(tags=["deliveries"])

Receiver = Annotated[User, Depends(require("delivery.confirm"))]
Approver = Annotated[User, Depends(require("award.approve"))]


def _qty_milli(text: str) -> int:
    try:
        q = Fraction(Decimal(text.replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"Quantity '{text}' is not a number") from None
    milli = q * 1000
    if milli.denominator != 1 or milli < 0:
        raise HTTPException(422, "Quantity must be zero or more, with at most 3 decimals")
    return int(milli)


# --- vendor --------------------------------------------------------------------------------


@router.get("/vendor/work-orders")
def vendor_work_orders(vendor: VendorUser, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(WorkOrder)
        .where(WorkOrder.vendor_id == vendor.id)
        .order_by(WorkOrder.created_at.desc())
    )
    out = []
    for wo in rows:
        o = _wo_out(db, wo)
        o["received_milli"] = received_milli(db, wo)
        out.append(o)
    return out


class DispatchIn(BaseModel):
    client_ref: str = Field(min_length=6, max_length=80)
    vehicle_no: str = Field(min_length=4, max_length=20)
    qty: str | None = None  # blank = everything still to come


@router.post("/vendor/work-orders/{wo_id}/dispatch")
def vendor_dispatch(
    wo_id: uuid.UUID, body: DispatchIn, vendor: VendorUser, db: Db, clock: BizClock
) -> dict[str, Any]:
    wo = db.get(WorkOrder, wo_id)
    if wo is None or wo.vendor_id != vendor.id:
        raise HTTPException(404, "Not found")
    try:
        d = dispatch(
            db,
            clock,
            wo,
            body.vehicle_no,
            _qty_milli(body.qty) if body.qty else None,
            body.client_ref,
        )
    except DeliveryError as e:
        raise HTTPException(409, str(e)) from None
    db.commit()
    return {"delivery": d.public_code, "status": wo.status}


# --- builder -------------------------------------------------------------------------------


@router.post("/work-orders/{wo_id}/receive")
async def receive_delivery(
    request: Request, wo_id: uuid.UUID, qty: str, user: Receiver, db: Db, clock: BizClock
) -> dict[str, Any]:
    """Received quantity + a photo (raw image bytes in the body)."""
    wo = get_owned(db, WorkOrder, wo_id, org_id(user))
    photo = await request.body()
    if not photo:
        raise HTTPException(422, "A photo of the delivery is required")
    if len(photo) > MAX_FILE_BYTES:
        raise HTTPException(413, "The photo is larger than 10 MB")
    try:
        mime = sniff(photo)
    except DocumentError:
        raise HTTPException(422, "The photo must be a JPG, PNG or WEBP image") from None
    if not mime.startswith("image/"):
        raise HTTPException(422, "The photo must be a JPG, PNG or WEBP image")
    ref = get_file_store().save(
        f"deliveries/{wo.builder_org_id}", "photo." + mime.split("/")[1], photo
    )
    try:
        d = receive(db, clock, wo, _qty_milli(qty), ref, user.id)
    except DeliveryError as e:
        raise HTTPException(409, str(e)) from None
    db.commit()
    return {
        "delivery": d.public_code,
        "received_milli": d.received_qty_milli,
        "flags": d.flags,
        "work_order": _wo_out(db, wo),
    }


@router.get("/deliveries/{delivery_id}/photo")
def delivery_photo(delivery_id: uuid.UUID, user: Receiver, db: Db) -> Response:
    d = get_owned(db, Delivery, delivery_id, org_id(user))
    if not d.received_photo_ref:
        raise HTTPException(404, "Not found")
    ext = d.received_photo_ref.rsplit(".", 1)[-1]
    return Response(
        get_file_store().read(d.received_photo_ref),
        media_type=f"image/{'jpeg' if ext == 'jpeg' else ext}",
    )


@router.post("/work-orders/{wo_id}/invoice")
async def upload_invoice(
    request: Request, wo_id: uuid.UUID, invoice_no: str, po_code: str, amount: str, unit_price: str,
    client_ref: str, user: Receiver, db: Db, clock: BizClock, filename: str = "invoice.pdf",
) -> dict[str, Any]:  # fmt: skip
    """Invoice details as typed from the vendor's invoice; the file (optional) in the body."""
    wo = get_owned(db, WorkOrder, wo_id, org_id(user))
    data = await request.body()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB")
    ref = None
    if data:
        try:
            sniff(data)
        except DocumentError:
            raise HTTPException(422, "Upload the invoice as a PDF or photo") from None
        ref = get_file_store().save(f"invoices/{wo.builder_org_id}", filename, data)
    try:
        inv_in = InvoiceIn(
            invoice_no, po_code, rupees_to_paise(amount), rupees_to_paise(unit_price)
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    inv = check_invoice(db, clock, wo, inv_in, ref, client_ref)
    db.commit()
    return {"id": str(inv.id), "status": inv.status, "mismatch_flags": inv.mismatch_flags}


@router.get("/invoices/{invoice_id}/file")
def invoice_file(invoice_id: uuid.UUID, user: Receiver, db: Db) -> Response:
    inv = get_owned(db, Invoice, invoice_id, org_id(user))
    if not inv.file_ref:
        raise HTTPException(404, "Not found")
    return Response(get_file_store().read(inv.file_ref), media_type="application/octet-stream")


class AcceptIn(BaseModel):
    note: str = Field(min_length=3, max_length=300)


@router.post("/invoices/{invoice_id}/accept")
def accept_invoice(
    invoice_id: uuid.UUID, body: AcceptIn, user: Approver, db: Db, clock: BizClock
) -> dict[str, Any]:
    """A flagged invoice is never accepted automatically: a person does it, with a reason."""
    inv = get_owned(db, Invoice, invoice_id, org_id(user))
    if inv.status != "flagged":
        raise HTTPException(409, f"Invoice is {inv.status}")
    inv.status, inv.accepted_by, inv.accept_note = "accepted", user.id, body.note
    audit(db, clock, actor=f"user:{user.id}", action="invoice.accepted", entity="invoice", entity_id=inv.id,
          org_id=inv.builder_org_id, after={"note": body.note, "flags": inv.mismatch_flags})  # fmt: skip
    db.commit()
    return {"id": str(inv.id), "status": inv.status}


class CloseIn(BaseModel):
    shortfall_note: str | None = Field(default=None, max_length=300)


@router.post("/work-orders/{wo_id}/close")
def close_work_order(
    wo_id: uuid.UUID, body: CloseIn, user: Approver, db: Db, clock: BizClock
) -> dict[str, Any]:
    wo = get_owned(db, WorkOrder, wo_id, org_id(user))
    try:
        close(db, clock, wo, user.id, body.shortfall_note)
    except DeliveryError as e:
        raise HTTPException(409, str(e)) from None
    db.commit()
    return _wo_out(db, wo)
