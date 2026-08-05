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


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="op", password="pw12345", display_name="Op"))
    db_session.flush()
    r = client.post("/login", data={"username": "op", "password": "pw12345"})
    assert r.status_code == 204
    assert r.headers.get("HX-Redirect") == "/"


def test_home_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LightMES" in resp.text
    # home nav must link every master-data management page + ERP import
    for href in [
        "/masterdata/products",
        "/masterdata/lines",
        "/masterdata/work-stations",
        "/masterdata/routings",
        "/masterdata/boms",
        "/production/sn-rules",
        "/integration/import",
    ]:
        assert href in resp.text


def test_products_page_renders(client):
    resp = client.get("/masterdata/products")
    assert resp.status_code == 200
    assert "产品管理" in resp.text


def test_create_product_via_page_returns_row(client, db_session):
    _login(client, db_session)
    resp = client.post(
        "/masterdata/products",
        data={"code": "UI-1", "name": "壳", "type": "finished",
              "unit": "pcs", "track_mode": "none"},
    )
    assert resp.status_code == 200
    assert "UI-1" in resp.text


def test_create_product_dup_error_escapes_html(client, db_session):
    _login(client, db_session)
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


def test_create_product_via_page_requires_login(client):
    resp = client.post(
        "/masterdata/products",
        data={"code": "NO-LOGIN", "name": "x", "type": "component",
              "unit": "pcs", "track_mode": "none"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"
