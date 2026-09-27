import uuid
from fractions import Fraction

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import CatalogItem, UnitConversion
from app.domain.bom_rows import CatalogIndex, ItemInfo
from app.domain.units import Conversion

# Best trigram similarity between the text and an item's name or any alias.
_SUGGEST = text("""
    SELECT c.id, max(similarity(t.term, :q)) AS score
    FROM catalog_items c, LATERAL unnest(array_append(c.aliases, c.name)) AS t(term)
    GROUP BY c.id
    HAVING max(similarity(t.term, :q)) >= 0.15
    ORDER BY score DESC, c.id
    LIMIT 3
""")


def item_info(c: CatalogItem) -> ItemInfo:
    return ItemInfo(c.id, c.code, c.name, c.grade, c.canonical_unit, tuple(c.aliases))


def conversions(db: Session) -> dict[uuid.UUID | None, list[Conversion]]:
    out: dict[uuid.UUID | None, list[Conversion]] = {}
    for uc in db.scalars(select(UnitConversion)):
        out.setdefault(uc.item_id, []).append(
            Conversion(uc.from_unit, uc.to_unit, Fraction(uc.numerator, uc.denominator))
        )
    return out


def build_index(db: Session) -> CatalogIndex:
    items = [item_info(c) for c in db.scalars(select(CatalogItem).order_by(CatalogItem.name))]
    by_id = {i.id: i for i in items}

    def suggest(q: str) -> list[ItemInfo]:
        rows = db.execute(_SUGGEST, {"q": q.lower()}).all()
        return [by_id[r.id] for r in rows if r.id in by_id]

    return CatalogIndex(items=items, conversions=conversions(db), suggest=suggest)
