"""MessageChannel: how agents talk to vendors. Simulated now, WhatsApp Cloud API later."""

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.db.models import Message, Vendor
from app.jobs.clock import Clock

WINDOW_HOURS = 24


class OutsideWindow(Exception):
    """Free text is only allowed within 24 h of the vendor's last message."""


@dataclass
class Outbound:
    vendor: Vendor
    template: str | None = None  # None = free text (needs an open 24 h window)
    params: dict[str, str] = field(default_factory=dict)
    text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    org_id: uuid.UUID | None = None
    rfq_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None
    invitation_id: uuid.UUID | None = None


class MessageChannel(Protocol):
    name: str

    def send(self, db: Session, clock: Clock, msg: Outbound) -> Message:
        """Store and deliver. Returns the Message; status 'blocked' if not allowed."""
        ...
