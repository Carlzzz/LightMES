import pytest
from fastapi.testclient import TestClient

from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate


@pytest.fixture()
def client(db_session):
    # 用测试事务 session 覆盖 get_db 依赖
    app.dependency_overrides[get_db] = lambda: db_session
    # 预置一个用户
    AuthService(db_session).create_user(
        UserCreate(username="eve", password="pw12345", display_name="Eve")
    )
    db_session.flush()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_login_success(client):
    resp = client.post(
        "/api/auth/login", data={"username": "eve", "password": "pw12345"}
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "eve"


def test_api_login_failure(client):
    resp = client.post(
        "/api/auth/login", data={"username": "eve", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "LightMES 登录" in resp.text


def test_login_submit_success_returns_welcome(client):
    resp = client.post("/login", data={"username": "eve", "password": "pw12345"})
    assert resp.status_code == 200
    assert "欢迎" in resp.text


def test_login_submit_failure_returns_error_fragment(client):
    resp = client.post("/login", data={"username": "eve", "password": "wrong"})
    assert resp.status_code == 200
    assert "用户名或密码错误" in resp.text
