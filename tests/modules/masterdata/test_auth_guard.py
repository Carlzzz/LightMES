import pytest
from fastapi.testclient import TestClient

from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate


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


def test_api_create_routing_requires_login(client):
    resp = client.post(
        "/api/masterdata/routings",
        json={
            "code": "RT-X",
            "name": "x",
            "product_id": 1,
            "operations": [
                {"seq": 1, "code": "OP1", "name": "s1", "default_work_station_id": 1},
            ],
        },
    )
    assert resp.status_code == 401


def test_api_create_bom_requires_login(client):
    resp = client.post(
        "/api/masterdata/boms",
        json={"product_id": 1, "items": []},
    )
    assert resp.status_code == 401


def _login(client, db_session, username="op", password="pw12345", display_name="Op"):
    user = AuthService(db_session).create_user(
        UserCreate(username=username, password=password, display_name=display_name)
    )
    db_session.flush()
    resp = client.post(
        "/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 204
    return user


def test_deactivated_user_cannot_create_via_api(client, db_session):
    user = _login(client, db_session)
    user.is_active = False
    db_session.flush()
    resp = client.post(
        "/api/masterdata/products",
        json={"code": "D-API", "name": "x", "type": "component",
              "unit": "pcs", "track_mode": "none"},
    )
    assert resp.status_code == 401


def test_deactivated_user_cannot_create_via_page(client, db_session):
    user = _login(client, db_session, username="op2", display_name="Op2")
    user.is_active = False
    db_session.flush()
    resp = client.post(
        "/masterdata/products",
        data={"code": "D-PAGE", "name": "x", "type": "component",
              "unit": "pcs", "track_mode": "none"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"].startswith("/login")
