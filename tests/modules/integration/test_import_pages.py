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
        UserCreate(username="imp", password="pw12345", display_name="Imp"))
    db_session.flush()
    assert client.post("/login", data={"username": "imp", "password": "pw12345"}).status_code == 204


def test_import_page_renders(client, db_session):
    resp = client.get("/integration/import")
    assert resp.status_code == 200
    assert "主数据导入" in resp.text


def test_import_products_api_requires_login(client, db_session):
    resp = client.post("/api/integration/import/products",
        files={"file": ("p.csv", b"erp_ref,code,name,type\n", "text/csv")})
    assert resp.status_code == 401


def test_import_products_page_success(client, db_session):
    _login(client, db_session)
    csv = b"erp_ref,code,name,type,unit,track_mode\nERP-1,P1,\xe4\xbb\xb6,component,pcs,serial\n"
    resp = client.post("/integration/import",
        data={"kind": "products"}, files={"file": ("p.csv", csv, "text/csv")})
    assert resp.status_code == 200
    assert "新增 1" in resp.text


def test_import_page_requires_login_on_post(client, db_session):
    resp = client.post("/integration/import",
        data={"kind": "products"}, files={"file": ("p.csv", b"x", "text/csv")})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"
