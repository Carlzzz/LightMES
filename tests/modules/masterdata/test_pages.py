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


def test_create_product_dup_error_escapes_html(client, db_session):
    # first create a product whose code contains an HTML/script payload
    payload = "<img src=x onerror=alert(1)>"
    client.post("/masterdata/products", data={
        "code": payload, "name": "x", "type": "component",
        "unit": "pcs", "track_mode": "none"})
    # second create with same code triggers the dup-code error fragment
    resp = client.post("/masterdata/products", data={
        "code": payload, "name": "y", "type": "component",
        "unit": "pcs", "track_mode": "none"})
    assert resp.status_code == 200
    # payload must be escaped, not reflected raw
    assert "<img src=x onerror=alert(1)>" not in resp.text
    assert "&lt;img" in resp.text
