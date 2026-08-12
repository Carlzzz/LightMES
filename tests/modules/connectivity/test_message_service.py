from datetime import datetime, timezone

from lightmes.modules.connectivity.models import (
    MachineConnection,
    MachineMessage,
    MachineTopic,
)
from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message


def _conn(db_session, name="msg-test"):
    c = MachineConnection(name=name, is_active=True)
    db_session.add(c)
    db_session.flush()
    return c


def test_persist_message_with_matching_topic(db_session):
    c = _conn(db_session, "match")
    t = MachineTopic(
        machine_connection_id=c.id,
        topic_pattern="machine/+/count",
        payload_format="json",
        is_active=True,
    )
    db_session.add(t)
    db_session.commit()  # 让独立 session 看到这条
    result = persist_message(
        c.id, "machine/L1/count", b'{"count": 1}', datetime.now(timezone.utc)
    )
    assert result.status == "ok"
    assert result.matched_topic_id == t.id


def test_persist_message_no_match_status_skipped(db_session):
    c = _conn(db_session, "no-match")
    # 加一个不匹配的 topic
    t = MachineTopic(
        machine_connection_id=c.id,
        topic_pattern="machine/other",
        payload_format="json",
        is_active=True,
    )
    db_session.add(t)
    db_session.commit()  # 让独立 session 看到这条
    result = persist_message(
        c.id, "machine/L1/count", b'{"count": 1}', datetime.now(timezone.utc)
    )
    assert result.status == "skipped"
    assert result.matched_topic_id is None


def test_persist_message_increments_count(db_session):
    from sqlalchemy import select

    from lightmes.database import SessionLocal
    from lightmes.modules.connectivity.models import MachineConnection as MC

    c = _conn(db_session, "count")
    db_session.commit()  # 让独立 session 看到这条

    persist_message(c.id, "machine/x", b"x", datetime.now(timezone.utc))
    persist_message(c.id, "machine/x", b"y", datetime.now(timezone.utc))

    db = SessionLocal()
    try:
        conn = db.get(MC, c.id)
        assert conn.messages_received == 2
        msgs = list(
            db.execute(
                select(MachineMessage).where(
                    MachineMessage.machine_connection_id == c.id
                )
            ).scalars().all()
        )
        assert len(msgs) == 2
    finally:
        db.close()


def test_persist_message_invalid_utf8_payload(db_session):
    """二进制 payload（非 UTF-8）应该用 replace 策略，不抛异常。"""
    c = _conn(db_session, "binary")
    db_session.commit()
    result = persist_message(
        c.id, "machine/binary", b"\xff\xfe\x00binary", datetime.now(timezone.utc)
    )
    assert result.status in ("ok", "skipped")
    assert result.error is None


def test_persist_message_handles_db_failure_gracefully(db_session):
    """connection_id 不存在 → 持久化失败但函数不抛异常。"""
    result = persist_message(99999, "machine/x", b"y", datetime.now(timezone.utc))
    assert result.status == "error"
    assert result.error is not None


def test_persist_message_with_mapping_log_event(db_session):
    """Message with active mapping → parsed_data + actions_triggered stored."""
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineTopic, TopicMapping, MachineMessage as MM)
    c = MachineConnection(name="pe-log", is_active=True)
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="test/topic",
                     payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    m = TopicMapping(machine_topic_id=t.id, action_type="log_event",
                     field_path="$.event", priority=100, is_active=True)
    db_session.add(m); db_session.commit()

    result = persist_message(c.id, "test/topic", b'{"event": "cycle_done"}',
                             datetime.now(timezone.utc))
    assert result.status == "ok"
    # Verify stored message has parsed_data + actions_triggered
    from lightmes.database import SessionLocal
    import sqlalchemy
    db = SessionLocal()
    try:
        msg = db.execute(
            sqlalchemy.select(MM).where(
                MM.machine_connection_id == c.id)
        ).scalars().first()
        assert msg.parsed_data == {"event": "cycle_done"}
        assert msg.actions_triggered is not None
        assert len(msg.actions_triggered) == 1
        assert msg.actions_triggered[0]["status"] == "ok"
    finally:
        db.close()


def test_persist_message_condition_not_met(db_session):
    """Mapping with condition not met → status=skipped."""
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineTopic, TopicMapping)
    c = MachineConnection(name="pe-cond", is_active=True)
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="test/c",
                     payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    m = TopicMapping(machine_topic_id=t.id, action_type="log_event",
                     field_path="$.count", condition_expr="value > 100",
                     priority=100, is_active=True)
    db_session.add(m); db_session.commit()

    result = persist_message(c.id, "test/c", b'{"count": 5}',
                             datetime.now(timezone.utc))
    assert result.status == "skipped"


def test_persist_message_action_error_continues(db_session):
    """One mapping errors → recorded, others continue. Overall status=ok if any succeed."""
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineTopic, TopicMapping)
    c = MachineConnection(name="pe-err", is_active=True)
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="test/e",
                     payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    # bad mapping (references nonexistent WO)
    db_session.add(TopicMapping(
        machine_topic_id=t.id, action_type="update_work_order_produced_qty",
        action_params={"work_order_code": "NOSUCH", "qty_increment": True},
        field_path="$.qty", priority=100, is_active=True))
    # good mapping
    db_session.add(TopicMapping(
        machine_topic_id=t.id, action_type="log_event",
        priority=200, is_active=True))
    db_session.commit()

    result = persist_message(c.id, "test/e", b'{"qty": 1}',
                             datetime.now(timezone.utc))
    # At least one ok → overall ok
    assert result.status == "ok"
