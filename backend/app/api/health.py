from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db

router = APIRouter()


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    db.execute(text("SELECT 1"))
    s = get_settings()
    return {"status": "ok", "db": "ok", "demo_mode": s.demo_mode, "llm": s.llm_provider}
