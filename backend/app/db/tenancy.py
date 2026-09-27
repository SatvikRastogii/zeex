import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session


def get_owned[M](db: Session, model: type[M], row_id: uuid.UUID, org_id: uuid.UUID) -> M:
    """Fetch a tenant-owned row or 404. Another tenant's row is indistinguishable
    from a missing one (no 403, so existence never leaks)."""
    m: Any = model
    row = db.scalars(select(m).where(m.id == row_id, m.builder_org_id == org_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Not found")
    return row
