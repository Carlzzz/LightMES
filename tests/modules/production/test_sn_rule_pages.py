import pytest
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.service import AuthService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="sn", password="pw12345", display_name="Sn"))
    db_session.flush()
    assert client.post(
        "/login", data={"username": "sn", "password": "pw12345"}).status_code == 204


def test_sn_rules_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/production/sn-rules")
    assert resp.status_code == 200
    assert "SN 规则管理" in resp.text


def test_sn_rules_page_and_create(client, db_session):
    _login(client, db_session)
    resp = client.post("/production/sn-rules",
        data={"code": "R1", "name": "规则", "pattern": "SN{SEQ:5}", "seq_reset": "never"})
    assert resp.status_code == 200 and "R1" in resp.text


def test_sn_rules_create_requires_login(client):
    resp = client.post("/production/sn-rules",
        data={"code": "R2", "name": "规则", "pattern": "SN{SEQ:5}", "seq_reset": "never"},
        headers={"HX-Request": "true"})
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"].startswith("/login")


def test_sn_rules_create_bad_pattern_returns_error_row(client, db_session):
    _login(client, db_session)
    resp = client.post("/production/sn-rules",
        data={"code": "R3", "name": "规则", "pattern": "BAD", "seq_reset": "never"})
    assert resp.status_code == 200
    assert "pattern" in resp.text
    assert "alert--danger" in resp.text
