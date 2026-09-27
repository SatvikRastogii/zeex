"""Human-readable public IDs backed by Postgres sequences (never max()+1)."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.jobs.clock import Clock, ist_year

# kind -> (prefix, sequence, digits)
_KINDS = {
    "bom": ("BOM", "bom_code_seq", 5),
    "quote": ("QT", "quote_code_seq", 6),
    "negotiation": ("NEG", "neg_code_seq", 6),
    "work_order": ("PO", "po_code_seq", 5),
}


def next_code(db: Session, kind: str, clock: Clock) -> str:
    """next_code(db, "bom", clock) -> 'BOM-2026-00042'. Year is the IST year now."""
    prefix, seq, digits = _KINDS[kind]
    n: int = db.execute(text(f"SELECT nextval('{seq}')")).scalar_one()  # noqa: S608  fixed names
    return f"{prefix}-{ist_year(clock)}-{n:0{digits}d}"


def rfq_code(bom_code: str, line_no: int) -> str:
    """RFQ per BOM line: BOM-2026-00042 line 3 -> RFQ-2026-00042-03."""
    return f"RFQ-{bom_code.removeprefix('BOM-')}-{line_no:02d}"


def delivery_code(po_code: str, seq: int) -> str:
    """PO-2026-00031 delivery 1 -> DLV-2026-00031-1."""
    return f"DLV-{po_code.removeprefix('PO-')}-{seq}"
