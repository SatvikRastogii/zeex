import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditLog
from app.jobs.clock import Clock


def audit(
    db: Session,
    clock: Clock,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: uuid.UUID | str | None = None,
    org_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            builder_org_id=org_id,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id else None,
            before=before,
            after=after,
            at=clock.now(),
        )
    )
