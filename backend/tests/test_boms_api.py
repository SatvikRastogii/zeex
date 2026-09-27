import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Bom, BuilderOrg, Rfq, Site
from tests.phones import GREENLINE_OWNER, SHARMA_OWNER, SHARMA_PM, SHARMA_SE, VENDOR_BALAJI

Login = Callable[[str], TestClient]
OK_DATE = "2026-10-05"  # business clock is 25 Sep 2026


def sharma_site(db: Session, name: str = "Noida Sector 62 Tower") -> uuid.UUID:
    return db.scalars(select(Site.id).where(Site.name == name)).one()


def rows(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    base = [
        {"item": "OPC 53 Grade Cement", "quantity": "30", "unit": "bag", "needed_by": OK_DATE},
        {
            "item": "saria",
            "spec": "Fe 500D",
            "quantity": "2.5",
            "unit": "tonne",
            "needed_by": OK_DATE,
        },
    ]
    return base + list(extra)


def create(
    c: TestClient, site_id: uuid.UUID, body_rows: list[dict[str, Any]], ref: str | None = None
) -> Any:
    return c.post("/api/boms", json={"site_id": str(site_id), "rows": body_rows, "title": "Tower A",
                                     "client_ref": ref or uuid.uuid4().hex})  # fmt: skip


@pytest.fixture
def owner(login: Login) -> TestClient:
    return login(SHARMA_OWNER)


def test_templates_download(owner: TestClient) -> None:
    csv = owner.get("/api/boms/template.csv")
    assert csv.status_code == 200 and csv.text.startswith("item,spec,quantity")
    xlsx = owner.get("/api/boms/template.xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"


def test_catalog_listed(owner: TestClient) -> None:
    assert len(owner.get("/api/catalog").json()) == 12


def test_validate_file_ok(owner: TestClient, seeded: Session) -> None:
    data = b"item,quantity,unit,needed_by\ncement 53 grade,30,bag,2026-10-05\nsand,2,brass,05/10/2026\n"
    r = owner.post(
        f"/api/boms/validate-file?site_id={sharma_site(seeded)}&filename=bom.csv", content=data
    )
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["file_ref"].startswith("boms/")
    assert [x["qty_canonical_milli"] for x in body["rows"]] == [30_000, 200_000]


@pytest.mark.parametrize(
    ("data", "name", "status", "msg"),
    [
        (b"", "bom.csv", 422, "empty"),
        (b"a,b\n1,2\n", "bom.csv", 422, "Missing column"),
        (b"%PDF", "bom.pdf", 422, ".csv or .xlsx"),
        (b"x" * (5 * 1024 * 1024 + 1), "bom.csv", 413, "5 MB"),
    ],
    ids=["empty", "wrong-columns", "wrong-type", "too-large"],
)
def test_validate_file_rejects(
    owner: TestClient, seeded: Session, data: bytes, name: str, status: int, msg: str
) -> None:
    r = owner.post(
        f"/api/boms/validate-file?site_id={sharma_site(seeded)}&filename={name}", content=data
    )
    assert r.status_code == status and msg in r.json()["detail"]


def test_unknown_item_gets_trigram_suggestions(owner: TestClient, seeded: Session) -> None:
    body = {"site_id": str(sharma_site(seeded)),
            "rows": [{"item": "cemnt opc", "quantity": "5", "unit": "bag", "needed_by": OK_DATE}]}  # fmt: skip
    row = owner.post("/api/boms/validate", json=body).json()["rows"][0]
    assert row["errors"][0]["field"] == "item"
    assert "OPC53" in [s["code"] for s in row["suggestions"]]


def test_create_bom(owner: TestClient, seeded: Session) -> None:
    r = create(owner, sharma_site(seeded), rows())
    assert r.status_code == 201, r.text
    bom = r.json()
    assert bom["code"] == "BOM-2026-00001" and bom["status"] == "validated"
    assert [ln["qty_display"] for ln in bom["lines"]] == ["30 bag", "2.5 tonne"]


def test_create_with_errors_creates_nothing(owner: TestClient, seeded: Session) -> None:
    bad = rows({"item": "sand", "quantity": "-1", "unit": "truck", "needed_by": "2020-01-01"})
    r = create(owner, sharma_site(seeded), bad)
    assert r.status_code == 422
    assert r.json()["detail"]["rows"][2]["errors"]
    assert seeded.scalars(select(Bom)).first() is None


def test_create_is_idempotent(owner: TestClient, seeded: Session) -> None:
    first = create(owner, sharma_site(seeded), rows(), ref="same-ref-123")
    again = create(owner, sharma_site(seeded), rows(), ref="same-ref-123")
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert len(seeded.scalars(select(Bom)).all()) == 1


def test_duplicate_lines_merged_on_create(owner: TestClient, seeded: Session) -> None:
    dup = rows({"item": "opc 53", "quantity": "20", "unit": "bag", "needed_by": OK_DATE})
    bom = create(owner, sharma_site(seeded), dup).json()
    assert [ln["qty_canonical_milli"] for ln in bom["lines"]] == [50_000, 2_500]


def test_site_of_another_org_is_404(owner: TestClient, seeded: Session) -> None:
    assert create(owner, sharma_site(seeded, "Dwarka Sector 19"), rows()).status_code == 404


def test_file_ref_of_another_org_rejected(owner: TestClient, seeded: Session) -> None:
    other = seeded.scalars(select(BuilderOrg.id).where(BuilderOrg.name == "Greenline Infra")).one()
    r = owner.post("/api/boms", json={"site_id": str(sharma_site(seeded)), "rows": rows(),
                                      "client_ref": uuid.uuid4().hex, "source": "upload",
                                      "file_ref": f"boms/{other}/x.csv"})  # fmt: skip
    assert r.status_code == 422


def test_upload_flow_keeps_original_file(owner: TestClient, seeded: Session) -> None:
    site = sharma_site(seeded)
    data = b"item,quantity,unit,needed_by\ncement 53 grade,30,bag,2026-10-05\n"
    v = owner.post(f"/api/boms/validate-file?site_id={site}&filename=bom.csv", content=data).json()
    r = owner.post("/api/boms", json={"site_id": str(site), "rows": [x["input"] for x in v["rows"]],
                                      "client_ref": uuid.uuid4().hex, "source": "upload",
                                      "file_ref": v["file_ref"]})  # fmt: skip
    assert r.status_code == 201
    assert owner.get(f"/api/boms/{r.json()['id']}/file").content == data


@pytest.mark.parametrize(
    ("phone", "status"), [(SHARMA_OWNER, 201), (SHARMA_PM, 201), (SHARMA_SE, 201)]
)
def test_every_builder_role_can_create(
    login: Login, seeded: Session, phone: str, status: int
) -> None:
    assert create(login(phone), sharma_site(seeded), rows()).status_code == status


def test_vendor_cannot_create(login: Login, seeded: Session) -> None:
    assert create(login(VENDOR_BALAJI), sharma_site(seeded), rows()).status_code == 403


def test_publish_creates_one_rfq_per_line(owner: TestClient, seeded: Session) -> None:
    bom = create(owner, sharma_site(seeded), rows()).json()
    pub = owner.post(f"/api/boms/{bom['id']}/publish").json()
    assert pub["status"] == "published"
    assert [ln["rfq"]["code"] for ln in pub["lines"]] == ["RFQ-2026-00001-01", "RFQ-2026-00001-02"]
    again = owner.post(f"/api/boms/{bom['id']}/publish")
    assert again.status_code == 200 and len(seeded.scalars(select(Rfq)).all()) == 2


def published(owner: TestClient, db: Session) -> dict[str, Any]:
    bom = create(owner, sharma_site(db), rows()).json()
    result: dict[str, Any] = owner.post(f"/api/boms/{bom['id']}/publish").json()
    return result


def test_edit_published_line_bumps_revision_and_marks_invited_rfq_stale(
    owner: TestClient, seeded: Session
) -> None:
    bom = published(owner, seeded)
    line = bom["lines"][0]
    seeded.execute(
        update(Rfq).where(Rfq.public_code == line["rfq"]["code"]).values(status="bidding")
    )
    seeded.commit()
    r = owner.patch(f"/api/boms/{bom['id']}/lines/{line['id']}", json={"quantity": "40"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["revision"] == 2
    assert out["lines"][0]["qty_canonical_milli"] == 40_000
    assert out["lines"][0]["rfq"] == {
        **line["rfq"],
        "status": "bidding",
        "stale": True,
        "revision": 2,
    }


def test_edit_draft_rfq_line_not_stale(owner: TestClient, seeded: Session) -> None:
    bom = published(owner, seeded)
    line = bom["lines"][0]
    out = owner.patch(
        f"/api/boms/{bom['id']}/lines/{line['id']}", json={"needed_by": "2026-10-10"}
    ).json()
    assert out["lines"][0]["rfq"]["stale"] is False and out["lines"][0]["needed_by"] == "2026-10-10"


def test_edit_line_validated(owner: TestClient, seeded: Session) -> None:
    bom = published(owner, seeded)
    r = owner.patch(f"/api/boms/{bom['id']}/lines/{bom['lines'][0]['id']}", json={"quantity": "0"})
    assert r.status_code == 422


def test_edit_locked_after_approval_stage(owner: TestClient, seeded: Session) -> None:
    bom = published(owner, seeded)
    line = bom["lines"][0]
    seeded.execute(
        update(Rfq).where(Rfq.public_code == line["rfq"]["code"]).values(status="awaiting_approval")
    )
    seeded.commit()
    r = owner.patch(f"/api/boms/{bom['id']}/lines/{line['id']}", json={"quantity": "40"})
    assert r.status_code == 409


def test_cancel_cancels_rfqs(owner: TestClient, seeded: Session) -> None:
    bom = published(owner, seeded)
    out = owner.post(f"/api/boms/{bom['id']}/cancel").json()
    assert out["status"] == "cancelled"
    assert {ln["rfq"]["status"] for ln in out["lines"]} == {"cancelled"}
    assert owner.post(f"/api/boms/{bom['id']}/publish").status_code == 409


def test_list_is_scoped(owner: TestClient, login: Login, seeded: Session) -> None:
    published(owner, seeded)
    assert len(owner.get("/api/boms").json()) == 1
    assert login(GREENLINE_OWNER).get("/api/boms").json() == []
