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


def _make_conn(db_session, name="t-detail"):
    from lightmes.modules.connectivity.models import MachineConnection, MqttConnection
    c = MachineConnection(name=name)
    db_session.add(c); db_session.flush()
    m = MqttConnection(machine_connection_id=c.id, broker_host="x", broker_port=1883)
    db_session.add(m); db_session.flush()
    return c


def test_connection_detail_renders(client, db_session):
    _login_admin(client, db_session, "d1")
    c = _make_conn(db_session, "detail-r")
    resp = client.get(f"/connectivity/connections/{c.id}")
    assert resp.status_code == 200
    assert "detail-r" in resp.text


def test_connection_detail_not_found(client, db_session):
    _login_admin(client, db_session, "d2")
    resp = client.get("/connectivity/connections/99999")
    assert resp.status_code == 404


def test_topic_add_via_post(client, db_session):
    _login_admin(client, db_session, "d3")
    c = _make_conn(db_session, "topic-add")
    resp = client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/+/count",
        "payload_format": "json",
    })
    assert resp.status_code in (200, 303)
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    assert t.topic_pattern == "machine/+/count"


def test_topic_toggle_via_post(client, db_session):
    _login_admin(client, db_session, "d4")
    c = _make_conn(db_session, "topic-tog")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    assert t.is_active is True
    # toggle → False
    resp = client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/toggle")
    assert resp.status_code in (200, 303)
    db_session.refresh(t)
    assert t.is_active is False


def test_topic_delete_via_post(client, db_session):
    _login_admin(client, db_session, "d5")
    c = _make_conn(db_session, "topic-del")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    resp = client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/delete")
    assert resp.status_code in (200, 303)
    assert db_session.get(MachineTopic, t.id) is None


def test_mapping_add_via_post(client, db_session):
    _login_admin(client, db_session, "m1")
    c = _make_conn(db_session, "mapping-add")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
            "action_type": "log_event", "priority": "50"})
    assert resp.status_code in (200, 303)
    from lightmes.modules.connectivity.models import TopicMapping
    m = db_session.query(TopicMapping).filter(
        TopicMapping.machine_topic_id == t.id).one()
    assert m.action_type == "log_event"
    assert m.priority == 50


def test_mapping_delete_via_post(client, db_session):
    _login_admin(client, db_session, "m2")
    c = _make_conn(db_session, "mapping-del")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
        "action_type": "log_event"})
    from lightmes.modules.connectivity.models import TopicMapping
    m = db_session.query(TopicMapping).filter(
        TopicMapping.machine_topic_id == t.id).one()
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings/{m.id}/delete")
    assert resp.status_code in (200, 303)
    assert db_session.get(TopicMapping, m.id) is None


def test_mapping_toggle_via_post(client, db_session):
    _login_admin(client, db_session, "m3")
    c = _make_conn(db_session, "map-tog")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
        "action_type": "log_event"})
    from lightmes.modules.connectivity.models import TopicMapping
    m = db_session.query(TopicMapping).filter(
        TopicMapping.machine_topic_id == t.id).one()
    assert m.is_active is True
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings/{m.id}/toggle")
    assert resp.status_code in (200, 303)
    db_session.refresh(m)
    assert m.is_active is False


def test_connection_detail_shows_mappings(client, db_session):
    """connection_detail page renders all_mappings section."""
    _login_admin(client, db_session, "m4")
    c = _make_conn(db_session, "map-view")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
        "action_type": "log_event", "field_path": "$.count"})
    resp = client.get(f"/connectivity/connections/{c.id}")
    assert resp.status_code == 200
    assert "Action Mappings" in resp.text
    assert "log_event" in resp.text


def test_mapping_add_invalid_action_type_rejected(client, db_session):
    """Invalid action_type → 400, no DB row created."""
    _login_admin(client, db_session, "m5")
    c = _make_conn(db_session, "map-invalid")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
            "action_type": "nonexistent_action"})
    assert resp.status_code == 400
    from lightmes.modules.connectivity.models import TopicMapping
    assert db_session.query(TopicMapping).filter(
        TopicMapping.machine_topic_id == t.id).count() == 0


def test_mapping_add_invalid_json_params_rejected(client, db_session):
    """action_params with malformed JSON → 400."""
    _login_admin(client, db_session, "m6")
    c = _make_conn(db_session, "map-bad-json")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
            "action_type": "log_event",
            "action_params": "{not valid json"})
    assert resp.status_code == 400


def test_connectivity_dashboard_renders(client, db_session):
    """Dashboard route renders overview for admin."""
    _login_admin(client, db_session, "dash1")
    c = _make_conn(db_session, "dash-conn")
    # 加一条消息用于显示
    from datetime import datetime, timezone
    from lightmes.modules.connectivity.models import MachineMessage
    db_session.add(MachineMessage(
        machine_connection_id=c.id, topic="t/x", raw_payload='{"k":1}',
        received_at=datetime.now(timezone.utc), processing_status="ok",
        parsed_data={"k": 1}))
    db_session.commit()
    resp = client.get("/connectivity/dashboard")
    assert resp.status_code == 200
    assert "数采看板" in resp.text
    assert "dash-conn" in resp.text
    assert "协议分布" in resp.text
    assert "连接状态汇总" in resp.text
