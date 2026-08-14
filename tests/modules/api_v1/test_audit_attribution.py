from sqlalchemy import select

import pytest
from fastapi.testclient import TestClient

from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import Role, User
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.audit import AuditLog
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin_user(db_session, username="audit_attribution_admin"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role)
        db_session.flush()
    user = User(
        username=username,
        password_hash=hash_password("pw12345"),
        display_name="Adm",
        is_active=True,
        role_id=role.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_api_v1_write_attributes_audit_log_to_api_key_owner(client, db_session):
    user = _admin_user(db_session)
    key, _ = ApiKeyService(db_session).create(
        name="audit-attribution-key",
        user_id=user.id,
        scopes=["read", "write"],
    )
    db_session.flush()

    response = client.post(
        "/api/v1/api-keys",
        headers={"Authorization": f"Bearer {key}"},
        json={"name": "Attributed Key", "scopes": ["read"]},
    )
    assert response.status_code == 201

    db_session.expire_all()
    log = db_session.execute(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "ApiKey",
            AuditLog.action == "created",
        )
        .order_by(AuditLog.id.desc())
    ).scalars().first()

    assert log is not None
    assert log.user_id == user.id
