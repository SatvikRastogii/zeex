"""Simulated WhatsApp: messages are stored and shown in the in-app Vendor Inbox."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.channels.base import WINDOW_HOURS, Outbound, OutsideWindow
from app.channels.templates import ALLOWED_WHEN_OPTED_OUT, TEMPLATES
from app.db.audit import audit
from app.db.models import Message, Vendor
from app.jobs.clock import Clock

log = logging.getLogger("channel")


def last_inbound_at(db: Session, vendor_id: uuid.UUID) -> datetime | None:
    return db.scalar(
        select(func.max(Message.sent_at)).where(
            Message.vendor_id == vendor_id, Message.direction == "in"
        )
    )


def window_open(db: Session, vendor_id: uuid.UUID, now: datetime) -> bool:
    last = last_inbound_at(db, vendor_id)
    return last is not None and now - last < timedelta(hours=WINDOW_HOURS)


def language(vendor: Vendor) -> str:
    return vendor.languages[0] if vendor.languages else "en"


# Called after every delivered outbound message (the demo's persona auto-reply hooks in here).
OUTBOUND_HOOKS: list[Callable[[Session, Clock, Message], None]] = []


class SimulatedWhatsAppChannel:
    name = "simulated_whatsapp"

    def send(self, db: Session, clock: Clock, msg: Outbound) -> Message:
        now = clock.now()
        v = msg.vendor
        in_window = window_open(db, v.id, now)
        if msg.template is None:
            if not in_window:
                raise OutsideWindow(
                    f"no message from {v.display_name} in the last {WINDOW_HOURS} h"
                )
            body = msg.text or ""
            buttons: tuple[str, ...] = ()
        else:
            tpl = TEMPLATES[msg.template]
            body = tpl.render(language(v), msg.params)
            buttons = tpl.buttons

        blocked = v.opted_out_at is not None and msg.template not in ALLOWED_WHEN_OPTED_OUT
        payload: dict[str, Any] = {**msg.payload}
        if buttons:
            payload.setdefault("buttons", list(buttons))
        m = Message(
            builder_org_id=msg.org_id,
            vendor_id=v.id,
            thread_id=msg.thread_id,
            invitation_id=msg.invitation_id,
            rfq_id=msg.rfq_id,
            direction="out",
            channel=self.name,
            external_message_id=f"sim-out-{uuid.uuid4().hex}",
            body=body,
            payload=payload,
            template_name=msg.template,
            in_24h_window=in_window,
            sent_at=now,
            delivered_at=None if blocked else now,
            status="blocked" if blocked else "sent",
        )
        db.add(m)
        db.flush()
        if blocked:
            log.warning("blocked %s to opted-out vendor %s", msg.template or "text", v.display_name)
            audit(
                db,
                clock,
                actor="system:channel",
                action="message.blocked_opted_out",
                entity="message",
                entity_id=m.id,
                org_id=msg.org_id,
                after={"vendor": v.display_name, "template": msg.template},
            )
        else:
            for hook in OUTBOUND_HOOKS:
                hook(db, clock, m)
        return m


_channel = SimulatedWhatsAppChannel()


def get_channel() -> SimulatedWhatsAppChannel:
    return _channel
