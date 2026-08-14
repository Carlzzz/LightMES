import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth.models import Role, User
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin(db_session, username):
    role = db_session.execute(
        select(Role).where(Role.name == "admin")
    ).scalar_one_or_none()
    if role is None:
        role = Role(name="admin", display_name="Admin", is_system=True)
        db_session.add(role)
        db_session.flush()

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


def test_settings_export_excludes_secret_fields(client, db_session):
    _admin(db_session, "backup_admin")
    _login(client, "backup_admin")

    resp = client.get("/api/system/settings/export")

    assert resp.status_code == 200
    body = resp.json()
    assert body["app_name"] == "LightMES"
    assert body["environment"] == "development"
    assert body["max_import_bytes"] > 0
    assert body["max_import_rows"] > 0
    assert body["session_max_age_seconds"] > 0
    assert "login_rate_limit" in body
    assert "api_rate_limit" in body
    assert "rate_limit_window_seconds" in body

    for secret in (
        "secret_key",
        "database_url",
        "admin_initial_password",
        "mqtt_url",
    ):
        assert secret not in body


def test_settings_export_requires_admin(client, db_session):
    resp = client.get("/api/system/settings/export")
    assert resp.status_code == 401


def test_db_dump_returns_403_when_disabled(client, db_session, monkeypatch):
    monkeypatch.delenv("ENABLE_DB_DUMP_API", raising=False)
    _admin(db_session, "backup_dump_admin")
    _login(client, "backup_dump_admin")

    resp = client.get("/api/system/db-dump")

    assert resp.status_code == 403
