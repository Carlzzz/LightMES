from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    ResolvedConnectionConfig,
    compute_config_signature,
    reconcile,
)
from lightmes.modules.connectivity.models import MachineConnection, MachineTopic


def _config(conn_id=1, host="x", port=1883, topics=None):
    return ResolvedConnectionConfig(
        connection_id=conn_id,
        protocol="mqtt",
        topics=topics or [],
        broker_host=host,
        broker_port=port,
        client_id="c1",
        username=None,
        password=None,
        use_tls=False,
        keep_alive_seconds=60,
        qos_default=0,
        clean_session=True,
        connect_timeout_seconds=10,
        reconnect_delay_seconds=5,
    )


def test_compute_signature_stable():
    a = _config()
    b = _config()
    assert compute_config_signature(a) == compute_config_signature(b)


def test_compute_signature_changes_with_host():
    a = _config(host="x")
    b = _config(host="y")
    assert compute_config_signature(a) != compute_config_signature(b)


def test_compute_signature_changes_with_topics():
    t = MachineTopic(id=1, machine_connection_id=1, topic_pattern="x", is_active=True)
    a = _config(topics=[])
    b = _config(topics=[t])
    assert compute_config_signature(a) != compute_config_signature(b)


def test_reconcile_spawns_new_connection(db_session):
    """新建 active connection → spawn_fn 被调用。"""
    from lightmes.modules.connectivity.models import MqttConnection

    c = MachineConnection(name="rec-1", is_active=True, protocol="mqtt")
    db_session.add(c)
    db_session.flush()
    db_session.add(
        MqttConnection(machine_connection_id=c.id, broker_host="x", broker_port=1883)
    )
    db_session.commit()

    spawned = []
    cancelled = []
    managed = {}
    sigs = {}
    reconcile(
        managed,
        sigs,
        spawn_fn=lambda cfg: spawned.append(cfg),
        cancel_fn=lambda cid: cancelled.append(cid),
    )
    assert len(spawned) == 1
    assert spawned[0].connection_id == c.id
    assert len(cancelled) == 0


def test_reconcile_cancels_removed_connection(db_session):
    """已 managed 的 connection 在 DB 中变成 inactive → cancel_fn 被调用。"""
    from lightmes.modules.connectivity.models import MqttConnection

    c = MachineConnection(name="rec-2", is_active=True, protocol="mqtt")
    db_session.add(c)
    db_session.flush()
    db_session.add(
        MqttConnection(machine_connection_id=c.id, broker_host="x", broker_port=1883)
    )
    db_session.commit()

    spawned = []
    cancelled = []
    managed = {}
    sigs = {}

    # spawn_fn / cancel_fn 必须维护 managed 字典（模拟真实 supervisor）
    def spawn(cfg):
        managed[cfg.connection_id] = cfg
        spawned.append(cfg)

    def cancel(cid):
        managed.pop(cid, None)
        sigs.pop(cid, None)
        cancelled.append(cid)

    # 第一次：spawn
    reconcile(managed, sigs, spawn, cancel)
    # 停用
    c.is_active = False
    db_session.commit()
    spawned.clear()
    cancelled.clear()
    # 第二次：cancel
    reconcile(managed, sigs, spawn, cancel)
    assert len(cancelled) == 1
    assert cancelled[0] == c.id
