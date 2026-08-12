import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin_with_key(db_session, username="apiadmin_ep"):
    """Create admin user + return (user, full_api_key)."""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("pw12345"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="admin-master", user_id=u.id, scopes=["read", "write"])
    return u, full_key


def test_api_keys_list(client, db_session):
    u, key = _admin_with_key(db_session)
    resp = client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(k["name"] == "admin-master" for k in data)
    # 列表项不含 key_hash 或 full_key
    assert "key_hash" not in data[0]
    assert "full_key" not in data[0]


def test_api_keys_create_returns_full_key(client, db_session):
    u, key = _admin_with_key(db_session, username="apiadmin_ep2")
    resp = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"}, json={
        "name": "Test Key",
        "scopes": ["read"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_key"].startswith("lmk_live_")
    assert data["name"] == "Test Key"
    assert data["scopes"] == ["read"]


def test_api_keys_revoke_via_delete(client, db_session):
    u, key = _admin_with_key(db_session, username="apiadmin_ep3")
    # 创建第二个 key
    _, record = ApiKeyService(db_session).create(name="To Revoke", user_id=u.id, scopes=["read"])
    db_session.flush()
    resp = client.delete(
        f"/api/v1/api-keys/{record.id}",
        headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code in (200, 204)
    db_session.refresh(record)
    assert record.is_active is False


def test_api_keys_create_readonly_key_forbidden(client, db_session):
    """read-only key 不能创建新 key。"""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    from lightmes.shared.security import hash_password
    u = User(username="ro_user", password_hash=hash_password("p"),
             display_name="RO", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    ro_key, _ = ApiKeyService(db_session).create(name="ro", user_id=u.id, scopes=["read"])
    resp = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {ro_key}"}, json={
        "name": "x", "scopes": ["read"],
    })
    assert resp.status_code == 403
