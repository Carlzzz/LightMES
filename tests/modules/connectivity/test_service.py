import pytest
from lightmes.modules.connectivity.service import ConnectivityService
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


def test_create_connection_returns_pair_with_encrypted_password(db_session):
    svc = ConnectivityService(db_session)
    conn, mqtt = svc.create_connection(
        name="t-conn-1", broker_host="broker.local", broker_port=1883,
        username="user", password="s3cret", use_tls=False)
    assert conn.id is not None
    assert conn.name == "t-conn-1"
    assert conn.protocol == "mqtt"
    assert mqtt.broker_host == "broker.local"
    assert mqtt.password_encrypted is not None
    assert mqtt.password_encrypted != "s3cret"  # 加密了


def test_create_connection_rejects_non_mqtt_protocol(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(ValidationError):
        svc.create_connection(name="bad", broker_host="x", broker_port=1883, protocol="opcua")


def test_create_connection_rejects_duplicate_name(db_session):
    svc = ConnectivityService(db_session)
    svc.create_connection(name="dup", broker_host="x", broker_port=1883)
    with pytest.raises(BusinessRuleError):
        svc.create_connection(name="dup", broker_host="y", broker_port=1883)


def test_create_connection_rejects_bad_port(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(ValidationError):
        svc.create_connection(name="bad-port", broker_host="x", broker_port=99999)


def test_create_connection_rejects_bad_qos(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(ValidationError):
        svc.create_connection(name="bad-qos", broker_host="x", broker_port=1883, qos_default=5)


def test_activate_and_deactivate(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="ad", broker_host="x", broker_port=1883)
    assert conn.is_active is False
    svc.activate_connection(conn.id)
    db_session.refresh(conn)
    assert conn.is_active is True
    svc.deactivate_connection(conn.id)
    db_session.refresh(conn)
    assert conn.is_active is False


def test_add_topic(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-topic", broker_host="x", broker_port=1883)
    t = svc.add_topic(conn.id, "machine/+/count", "json", "test topic")
    assert t.id is not None
    assert t.topic_pattern == "machine/+/count"
    assert t.is_active is True


def test_add_topic_rejects_invalid_format(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="bad-fmt", broker_host="x", broker_port=1883)
    with pytest.raises(ValidationError):
        svc.add_topic(conn.id, "machine/x", "xml")  # xml 不在 4 个允许值中


def test_add_topic_rejects_duplicate_active(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="dup-t", broker_host="x", broker_port=1883)
    svc.add_topic(conn.id, "machine/+/count", "json")
    # 同样 pattern + active=True 应该被拒
    with pytest.raises(BusinessRuleError):
        svc.add_topic(conn.id, "machine/+/count", "json")


def test_toggle_topic(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-toggle", broker_host="x", broker_port=1883)
    t = svc.add_topic(conn.id, "machine/x", "json")
    svc.toggle_topic(conn.id, t.id)
    db_session.refresh(t)
    assert t.is_active is False
    svc.toggle_topic(conn.id, t.id)
    db_session.refresh(t)
    assert t.is_active is True


def test_delete_topic(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-del", broker_host="x", broker_port=1883)
    t = svc.add_topic(conn.id, "machine/x", "json")
    svc.delete_topic(conn.id, t.id)
    assert svc.list_topics(conn.id) == []


def test_delete_connection_cascades(db_session):
    svc = ConnectivityService(db_session)
    conn, mqtt = svc.create_connection(name="cascade", broker_host="x", broker_port=1883)
    svc.add_topic(conn.id, "machine/x", "json")
    svc.delete_connection(conn.id)
    from lightmes.modules.connectivity.models import MqttConnection, MachineTopic
    assert db_session.get(MqttConnection, mqtt.id) is None
    assert svc.list_topics(conn.id) == []


def test_get_connection_not_found(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(NotFoundError):
        svc.get_connection(99999)


def test_list_recent_messages_empty(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-msg", broker_host="x", broker_port=1883)
    msgs = svc.list_recent_messages(conn.id)
    assert msgs == []
