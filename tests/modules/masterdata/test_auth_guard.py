import pytest
from fastapi.testclient import TestClient

from lightmes.main import app
from lightmes.database import get_db


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_create_product_requires_login(client):
    resp = client.post(
        "/api/masterdata/products",
        json={"code": "X", "name": "x", "type": "component",
              "unit": "pcs", "track_mode": "none"},
    )
    assert resp.status_code == 401


def test_api_create_work_order_requires_login(client):
    resp = client.post(
        "/api/production/work-orders",
        json={"code": "WO-X", "product_id": 1, "routing_id": 1, "qty": 1},
    )
    assert resp.status_code == 401


def test_api_release_work_order_requires_login(client):
    resp = client.post("/api/production/work-orders/1/release")
    assert resp.status_code == 401


def test_api_create_sn_rule_requires_login(client):
    resp = client.post(
        "/api/production/sn-rules",
        json={"code": "SR-X", "name": "x", "pattern": "SN{SEQ:4}"},
    )
    assert resp.status_code == 401
