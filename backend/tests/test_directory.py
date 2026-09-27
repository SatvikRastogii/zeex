from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.phones import ARORA_OWNER, GREENLINE_OWNER, SHARMA_OWNER, SHARMA_SE

Login = Callable[[str], TestClient]


def test_directory_shows_only_linked_vendors(login: Login, seeded: Session) -> None:
    arora = {v["name"] for v in login(ARORA_OWNER).get("/api/vendors").json()}
    sharma = {v["name"] for v in login(SHARMA_OWNER).get("/api/vendors").json()}
    assert "Gupta Building Materials" in sharma and "Gupta Building Materials" not in arora


def test_block_and_unblock_vendor(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    v = next(x for x in c.get("/api/vendors").json() if x["name"] == "Shree Balaji Cement Traders")
    out = c.patch(
        f"/api/vendors/{v['id']}", json={"status": "blocked", "notes": "late twice"}
    ).json()
    assert out["link_status"] == "blocked"
    assert (
        login(SHARMA_SE).patch(f"/api/vendors/{v['id']}", json={"status": "active"}).status_code
        == 403
    )
    assert any(
        a["action"] == "vendor.blocked" and a["actor"] == "Rakesh Sharma"
        for a in c.get("/api/audit").json()
    )


def test_cannot_touch_unlinked_vendor(login: Login, seeded: Session) -> None:
    gupta = next(
        x
        for x in login(SHARMA_OWNER).get("/api/vendors").json()
        if x["name"] == "Gupta Building Materials"
    )
    assert (
        login(ARORA_OWNER)
        .patch(f"/api/vendors/{gupta['id']}", json={"status": "blocked"})
        .status_code
        == 404
    )


def test_audit_is_scoped_and_filterable(login: Login, seeded: Session) -> None:
    c = login(SHARMA_OWNER)
    v = c.get("/api/vendors").json()[0]
    c.patch(f"/api/vendors/{v['id']}", json={"status": "blocked"})
    assert c.get("/api/audit?entity=vendor").json()
    assert c.get("/api/audit?entity=bom").json() == []
    assert login(GREENLINE_OWNER).get("/api/audit").json() == []
