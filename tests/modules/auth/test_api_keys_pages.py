import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.models import User, Role
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login_admin(client, db_session, username="akeyadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    AuthService(db_session).create_user(
        UserCreate(username=username, password="pw12345", display_name="Adm"))
    u = db_session.query(User).filter(User.username == username).one()
    u.role_id = role.id
    db_session.flush()
    client.post("/login", data={"username": username, "password": "pw12345"})


def test_api_keys_page_requires_login(client, db_session):
    resp = client.get("/system/api-keys", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_api_keys_page_renders_for_admin(client, db_session):
    _login_admin(client, db_session)
    resp = client.get("/system/api-keys")
    assert resp.status_code == 200
    assert "API Key" in resp.text or "api-keys" in resp.text


def test_api_key_create_via_post_returns_full_key(client, db_session):
    _login_admin(client, db_session, username="akeyadm2")
    resp = client.post("/system/api-keys", data={
        "name": "Test Key",
        "scopes": ["read", "write"],
    })
    assert resp.status_code in (200, 303)
    # full_key 在创建片段中显示一次
    assert b"lmk_live_" in resp.content or "lmk_live_" in resp.text


def test_api_key_revoke_via_post(client, db_session):
    _login_admin(client, db_session, username="akeyadm3")
    u = db_session.query(User).filter(User.username == "akeyadm3").one()
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    _, record = ApiKeyService(db_session).create(name="To Revoke", user_id=u.id, scopes=["read"])
    db_session.flush()
    resp = client.post(f"/system/api-keys/{record.id}/revoke")
    assert resp.status_code in (200, 303)
    db_session.refresh(record)
    assert record.is_active is False
