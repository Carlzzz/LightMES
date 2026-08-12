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
