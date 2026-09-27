import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import Db, VendorUser
from app.db.models import Message

router = APIRouter(prefix="/vendor", tags=["vendor"])


def _msg_out(m: Message) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "direction": m.direction,
        "body": m.body,
        "payload": m.payload,
        "template": m.template_name,
        "sent_at": m.sent_at,
        "rfq_id": m.rfq_id,
    }


@router.get("/messages")
def my_messages(vendor: VendorUser, db: Db) -> list[dict[str, Any]]:
    """A vendor sees only their own messages, across every builder they work with."""
    rows = db.scalars(
        select(Message).where(Message.vendor_id == vendor.id).order_by(Message.sent_at)
    )
    return [_msg_out(m) for m in rows]


@router.get("/messages/{message_id}")
def my_message(message_id: uuid.UUID, vendor: VendorUser, db: Db) -> dict[str, Any]:
    m = db.get(Message, message_id)
    if m is None or m.vendor_id != vendor.id:
        raise HTTPException(404, "Not found")
    return _msg_out(m)
