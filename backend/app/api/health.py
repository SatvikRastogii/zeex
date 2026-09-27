from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import BizClock, Db
from app.config import get_settings
from app.jobs.clock import format_ist
from app.llm.factory import provider_name

router = APIRouter()


@router.get("/health")
def health(db: Db, clock: BizClock) -> dict[str, object]:
    db.execute(text("SELECT 1"))
    s = get_settings()
    return {
        "status": "ok",
        "db": "ok",
        "demo_mode": s.demo_mode,
        "llm": provider_name(),
        "clock": format_ist(clock.now()),
    }
