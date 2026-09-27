from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_db_ok() -> None:
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["db"] == "ok"
