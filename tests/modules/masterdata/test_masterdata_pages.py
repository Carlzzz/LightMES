import pytest
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.service import AuthService
from lightmes.modules.masterdata.schemas import LineCreate
from lightmes.modules.masterdata.service import MasterDataService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="md", password="pw12345", display_name="Md"))
    db_session.flush()
    assert client.post(
        "/login", data={"username": "md", "password": "pw12345"}).status_code == 204


def test_lines_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/masterdata/lines")
    assert resp.status_code == 200
    assert "产线管理" in resp.text


def test_lines_page_and_create(client, db_session):
    _login(client, db_session)
    resp = client.post(
        "/masterdata/lines", data={"code": "L1", "name": "线1", "description": ""})
    assert resp.status_code == 200 and "L1" in resp.text


def test_lines_create_requires_login(client):
    resp = client.post(
        "/masterdata/lines", data={"code": "X", "name": "x", "description": ""})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"


def test_lines_create_dup_code_returns_error_row(client, db_session):
    _login(client, db_session)
    client.post("/masterdata/lines", data={"code": "DUP", "name": "线A", "description": ""})
    resp = client.post("/masterdata/lines", data={"code": "DUP", "name": "线B", "description": ""})
    assert resp.status_code == 200
    assert "产线编码已存在" in resp.text
    assert 'colspan="4"' in resp.text


def test_work_stations_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/masterdata/work-stations")
    assert resp.status_code == 200
    assert "作业站管理" in resp.text


def test_work_stations_page_and_create(client, db_session):
    _login(client, db_session)
    line = MasterDataService(db_session).create_line(
        LineCreate(code="WL", name="线WL"))
    resp = client.post("/masterdata/work-stations",
        data={"code": "WS1", "name": "站1", "line_id": str(line.id), "seq": "1"})
    assert resp.status_code == 200 and "WS1" in resp.text


def test_work_stations_create_requires_login(client):
    resp = client.post("/masterdata/work-stations",
        data={"code": "WSX", "name": "x", "line_id": "1", "seq": "1"})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"
