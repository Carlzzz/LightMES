import pytest
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth.models import Role, User
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(db_session, client, role_name="admin"):
    role = db_session.query(Role).filter(Role.name == role_name).first()
    if role is None:
        role = Role(name=role_name, display_name=role_name)
        db_session.add(role); db_session.flush()
    u = User(username=f"_eq_{role_name}", password_hash=hash_password("p"),
             display_name="E", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    db_session.commit()

    resp = client.post("/login", data={"username": u.username, "password": "p"})
    assert resp.status_code == 204


def test_monitor_page_ok(client, db_session):
    _login(db_session, client)
    resp = client.get("/equipment/monitor")
    assert resp.status_code == 200


def test_downtimes_page_ok(client, db_session):
    _login(db_session, client)
    resp = client.get("/equipment/downtimes")
    assert resp.status_code == 200


def test_tags_page_ok(client, db_session):
    _login(db_session, client)
    resp = client.get("/equipment/tags")
    assert resp.status_code == 200
