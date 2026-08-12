import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.production.models import DefectType
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _key(db_session):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username="dtadm", password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="dt-key", user_id=u.id, scopes=["read"])
    return full_key


def test_defect_types_list(client, db_session):
    db_session.add(DefectType(code="SCRATCH", name="刮花", category="外观",
                              severity="minor", is_active=True))
    db_session.add(DefectType(code="CRACK", name="裂纹", category="外观",
                              severity="critical", is_active=True))
    db_session.flush()
    key = _key(db_session)
    resp = client.get("/api/v1/defect-types",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    codes = {d["code"] for d in data}
    assert "SCRATCH" in codes
    assert "CRACK" in codes


def test_defect_types_list_filter_active(client, db_session):
    db_session.add(DefectType(code="ACTIVE1", name="A", category="外观",
                              severity="minor", is_active=True))
    db_session.add(DefectType(code="INACTIVE1", name="I", category="外观",
                              severity="minor", is_active=False))
    db_session.flush()
    key = _key(db_session)
    resp = client.get("/api/v1/defect-types?is_active=true",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    codes = {d["code"] for d in resp.json()}
    assert "ACTIVE1" in codes
    assert "INACTIVE1" not in codes
