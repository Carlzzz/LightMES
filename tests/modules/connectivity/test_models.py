from lightmes.modules.connectivity.models import (
    MachineConnection, MqttConnection, MachineTopic, MachineMessage,
    TopicMapping, OpcuaConnection, ModbusConnection,
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


def test_topic_mapping_basic_fields(db_session):
    from lightmes.modules.connectivity.models import MachineConnection, MachineTopic
    c = MachineConnection(name="tm-test")
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="x", payload_format="json")
    db_session.add(t); db_session.flush()
    m = TopicMapping(
        machine_topic_id=t.id, action_type="log_event",
        action_params={"key": "val"}, priority=50)
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.action_type == "log_event"
    assert m.priority == 50
    assert m.is_active is True


def test_machine_message_new_fields(db_session):
    from datetime import datetime, timezone
    from lightmes.modules.connectivity.models import MachineConnection
    c = MachineConnection(name="nm-test")
    db_session.add(c); db_session.flush()
    msg = MachineMessage(
        machine_connection_id=c.id, topic="t", raw_payload="p",
        received_at=datetime.now(timezone.utc),
        parsed_data={"count": 1},
        actions_triggered=[{"status": "ok"}],
        processing_error=None)
    db_session.add(msg); db_session.flush()
    assert msg.parsed_data == {"count": 1}
    assert msg.actions_triggered == [{"status": "ok"}]


def test_opcua_connection_basic_fields(db_session):
    c = MachineConnection(name="opcua-test", protocol="opcua")
    db_session.add(c); db_session.flush()
    o = OpcuaConnection(
        machine_connection_id=c.id,
        server_url="opc.tcp://192.168.1.10:4840",
        security_mode="none",
    )
    db_session.add(o); db_session.flush()
    assert o.id is not None
    assert o.server_url == "opc.tcp://192.168.1.10:4840"
    assert o.security_mode == "none"
    assert o.poll_interval_seconds == 5
    assert o.connect_timeout_seconds == 10
    assert o.reconnect_delay_seconds == 5


def test_modbus_connection_basic_fields(db_session):
    c = MachineConnection(name="modbus-test", protocol="modbus")
    db_session.add(c); db_session.flush()
    m = ModbusConnection(
        machine_connection_id=c.id,
        host="192.168.1.20",
    )
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.host == "192.168.1.20"
    assert m.port == 502
    assert m.slave_id == 1
    assert m.poll_interval_seconds == 5
