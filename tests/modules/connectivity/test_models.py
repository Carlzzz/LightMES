from lightmes.modules.connectivity.models import (
    MachineConnection, MqttConnection, MachineTopic, MachineMessage,
)


def test_machine_connection_basic_fields(db_session):
    c = MachineConnection(name="test-conn", protocol="mqtt")
    db_session.add(c); db_session.flush()
    assert c.id is not None
    assert c.protocol == "mqtt"
    assert c.is_active is False
    assert c.status == "disconnected"
    assert c.messages_received == 0


def test_mqtt_connection_basic_fields(db_session):
    c = MachineConnection(name="test-mqtt")
    db_session.add(c); db_session.flush()
    m = MqttConnection(
        machine_connection_id=c.id, broker_host="broker.local", broker_port=1883)
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.broker_host == "broker.local"
    assert m.broker_port == 1883
    assert m.keep_alive_seconds == 60
    assert m.qos_default == 0


def test_machine_topic_basic_fields(db_session):
    c = MachineConnection(name="test-topic")
    db_session.add(c); db_session.flush()
    t = MachineTopic(
        machine_connection_id=c.id, topic_pattern="machine/+/count",
        payload_format="json")
    db_session.add(t); db_session.flush()
    assert t.id is not None
    assert t.is_active is True
    assert t.topic_pattern == "machine/+/count"


def test_machine_message_basic_fields(db_session):
    from datetime import datetime, timezone
    c = MachineConnection(name="test-msg")
    db_session.add(c); db_session.flush()
    m = MachineMessage(
        machine_connection_id=c.id, topic="machine/L1/count",
        raw_payload='{"count": 1}',
        received_at=datetime.now(timezone.utc))
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.processing_status == "ok"
    assert m.matched_topic_id is None
