import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_home_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LightMES" in resp.text


def test_products_page_renders(client):
    resp = client.get("/masterdata/products")
    assert resp.status_code == 200
    assert "产品管理" in resp.text


def test_create_product_via_page_returns_row(client):
    resp = client.post(
        "/masterdata/products",
        data={"code": "UI-1", "name": "壳", "type": "finished",
              "unit": "pcs", "track_mode": "none"},
    )
    assert resp.status_code == 200
    assert "UI-1" in resp.text


def test_stations_page_renders(client):
    resp = client.get("/masterdata/stations")
    assert resp.status_code == 200
    assert "工位管理" in resp.text
