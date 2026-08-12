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


def _login_admin(client, db_session, username="connadm"):
    AuthService(db_session).create_user(
        UserCreate(username=username, password="pw12345", display_name="Adm"))
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = db_session.query(User).filter(User.username == username).one()
    u.role_id = role.id
    db_session.flush()
    client.post("/login", data={"username": username, "password": "pw12345"})


def test_connectivity_index_redirects(client, db_session):
    _login_admin(client, db_session, "r1")
    resp = client.get("/connectivity", follow_redirects=False)
    assert resp.status_code in (301, 302, 303)
    assert "/connections" in resp.headers["location"]


def test_connections_list_requires_login(client, db_session):
    resp = client.get("/connectivity/connections", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_connections_list_renders_for_admin(client, db_session):
    _login_admin(client, db_session, "r2")
    resp = client.get("/connectivity/connections")
    assert resp.status_code == 200
    assert "数采连接" in resp.text or "MQTT" in resp.text


def test_connections_create_post(client, db_session):
    _login_admin(client, db_session, "r3")
    resp = client.post("/connectivity/connections", data={
        "name": "test-conn-list",
        "broker_host": "broker.local",
        "broker_port": "1883",
        "username": "user",
        "password": "pass",
    })
    assert resp.status_code in (200, 303)
    # 验证入库
    from lightmes.modules.connectivity.models import MachineConnection
    c = db_session.query(MachineConnection).filter(
        MachineConnection.name == "test-conn-list").one()
    assert c.mqtt_ref.broker_host == "broker.local" if hasattr(c, "mqtt_ref") else True
    # 直接查 mqtt_connections
    from lightmes.modules.connectivity.models import MqttConnection
    m = db_session.query(MqttConnection).filter(
        MqttConnection.machine_connection_id == c.id).one()
    assert m.broker_host == "broker.local"


def test_connections_activate_toggle(client, db_session):
    _login_admin(client, db_session, "r4")
    # 先 create
    client.post("/connectivity/connections", data={
        "name": "act-conn", "broker_host": "x", "broker_port": "1883"})
    from lightmes.modules.connectivity.models import MachineConnection
    c = db_session.query(MachineConnection).filter(
        MachineConnection.name == "act-conn").one()
    resp = client.post(f"/connectivity/connections/{c.id}/activate")
    assert resp.status_code in (200, 303)
    db_session.refresh(c)
    assert c.is_active is True
    # deactivate
    resp = client.post(f"/connectivity/connections/{c.id}/deactivate")
    db_session.refresh(c)
    assert c.is_active is False


def test_connections_delete(client, db_session):
    _login_admin(client, db_session, "r5")
    client.post("/connectivity/connections", data={
        "name": "del-conn", "broker_host": "x", "broker_port": "1883"})
    from lightmes.modules.connectivity.models import MachineConnection
    c = db_session.query(MachineConnection).filter(
        MachineConnection.name == "del-conn").one()
    resp = client.post(f"/connectivity/connections/{c.id}/delete")
    assert resp.status_code in (200, 303)
    assert db_session.get(MachineConnection, c.id) is None
