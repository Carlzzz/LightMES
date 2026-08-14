import pytest
from fastapi.testclient import TestClient

from lightmes.main import app
from lightmes.database import get_db


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_realtime_shapes_returns_allowlist(client, db_session):
    resp = client.get("/api/realtime/shapes")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"work_orders_active", "serial_units_active", "defects_open"}
    assert data["serial_units_active"]["table"] == "serial_units"
