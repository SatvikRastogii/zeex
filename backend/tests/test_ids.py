from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.ids import delivery_code, next_code, rfq_code
from app.db.models import Bom
from app.db.session import get_engine
from app.jobs.clock import FixedClock
from tests.factories import org_with_site

CLOCK = FixedClock(datetime(2026, 9, 25, 8, 35, tzinfo=UTC))


def test_code_formats(db: Session) -> None:
    assert next_code(db, "bom", CLOCK) == "BOM-2026-00001"
    assert next_code(db, "quote", CLOCK) == "QT-2026-000001"
    assert next_code(db, "negotiation", CLOCK) == "NEG-2026-000001"
    assert next_code(db, "work_order", CLOCK) == "PO-2026-00001"
    assert rfq_code("BOM-2026-00042", 3) == "RFQ-2026-00042-03"
    assert delivery_code("PO-2026-00031", 1) == "DLV-2026-00031-1"


def test_year_segment_uses_ist(db: Session) -> None:
    nye = FixedClock(datetime(2026, 12, 31, 19, 0, tzinfo=UTC))  # 1 Jan 2027 IST
    assert next_code(db, "bom", nye).startswith("BOM-2027-")


def test_50_parallel_inserts_get_unique_codes(db: Session) -> None:
    org, site = org_with_site(db)
    make = sessionmaker(get_engine())

    def insert(_: int) -> str:
        with make() as s:
            code = next_code(s, "bom", CLOCK)
            s.add(Bom(builder_org_id=org.id, public_code=code, site_id=site.id))
            s.commit()
            return code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(insert, range(50)))

    assert len(set(codes)) == 50
    assert db.scalar(select(func.count(func.distinct(Bom.public_code)))) == 50
