from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth import dependencies as auth_deps
from lightmes.modules.auth.models import Role, User
from lightmes.shared.audit import AuditLog
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _role(db_session, name):
    role = db_session.execute(
        select(Role).where(Role.name == name)
    ).scalar_one_or_none()
    if role is None:
        role = Role(name=name, display_name=name)
        db_session.add(role)
        db_session.flush()
    return role


def _create_user(db_session, username, role_name="admin"):
    role = _role(db_session, role_name)
    user = User(
        username=username,
        password_hash=hash_password("pw12345"),
        display_name=username,
        role_id=role.id,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _login(client, username):
    resp = client.post("/login", data={"username": username, "password": "pw12345"})
    assert resp.status_code == 204


def _insert_logs(db_session, user_id=None):
    # User/Role creation also writes audit rows through the model listeners.
    # Clear those so the query assertions below only see the probe rows.
    db_session.execute(delete(AuditLog))
    db_session.flush()

    first = AuditLog(
        entity_type="User",
        entity_id=user_id,
        action="created",
        user_id=user_id,
        before_state=None,
        after_state={"username": "admin"},
        created_at=datetime(2026, 8, 14, 10, 0, 0),
        updated_at=datetime(2026, 8, 14, 10, 0, 0),
    )
    second = AuditLog(
        entity_type="Product",
        entity_id=1,
        action="updated",
        user_id=user_id,
        before_state={"name": "old"},
        after_state={"name": "new"},
        created_at=datetime(2026, 8, 14, 10, 1, 0),
        updated_at=datetime(2026, 8, 14, 10, 1, 0),
    )
    db_session.add_all([first, second])
    db_session.flush()
    return first, second


def test_page_returns_audit_rows_for_admin(client, db_session):
    admin = _create_user(db_session, "audit_admin")
    _login(client, "audit_admin")
    _insert_logs(db_session, user_id=admin.id)

    resp = client.get("/system/audit-logs")
    assert resp.status_code == 200
    assert "User" in resp.text
    assert "Product" in resp.text


def test_page_requires_login(client, db_session):
    resp = client.get("/system/audit-logs")
    assert resp.status_code == 401


def test_page_non_admin_forbidden(client, db_session, monkeypatch):
    monkeypatch.setattr(
        auth_deps, "get_settings", lambda: SimpleNamespace(environment="production")
    )
    _create_user(db_session, "audit_operator", role_name="operator")
    _login(client, "audit_operator")

    resp = client.get("/system/audit-logs")
    assert resp.status_code == 403


def test_api_returns_audit_rows_and_filters(client, db_session):
    admin = _create_user(db_session, "audit_api_admin")
    _login(client, "audit_api_admin")
    _insert_logs(db_session, user_id=admin.id)

    resp = client.get("/api/system/audit-logs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["entity_type"] == "Product"
    assert data[0]["user_id"] == admin.id

    filtered = client.get("/api/system/audit-logs", params={"entity_type": "User"})
    assert filtered.status_code == 200
    items = filtered.json()
    assert len(items) == 1
    assert items[0]["entity_type"] == "User"
    assert items[0]["user_id"] == admin.id

    limited = client.get("/api/system/audit-logs", params={"limit": 1})
    assert len(limited.json()) == 1


def test_api_requires_admin(client, db_session):
    resp = client.get("/api/system/audit-logs")
    assert resp.status_code == 401

    _create_user(db_session, "audit_api_operator", role_name="operator")
    _login(client, "audit_api_operator")
    resp = client.get("/api/system/audit-logs")
    assert resp.status_code == 403
