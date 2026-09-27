from typing import Any

from fastapi import APIRouter

from app.api.deps import Admin, BizClock
from app.jobs.clock import format_ist

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/clock")
def clock_state(_: Admin, clock: BizClock) -> dict[str, Any]:
    return {"now": clock.now(), "display": format_ist(clock.now())}
