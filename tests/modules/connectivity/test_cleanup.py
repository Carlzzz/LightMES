"""Tests for message retention cleanup functions."""
from datetime import datetime, timedelta, timezone

from lightmes.modules.connectivity.cleanup import (
    prune_old_messages,
    prune_per_connection,
)
from lightmes.modules.connectivity.models import MachineConnection, MachineMessage


def _conn(db_session, name):
    c = MachineConnection(name=name, is_active=True)
    db_session.add(c)
    db_session.flush()
    return c


def _msg(conn_id, days_ago, payload=b"x"):
    received = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return MachineMessage(
        machine_connection_id=conn_id,
        topic=f"t/{days_ago}",
        raw_payload=payload.decode("utf-8", errors="replace"),
        received_at=received,
        processing_status="ok",
    )


def test_prune_old_messages_deletes_only_old(db_session):
    """Messages older than retention_days are deleted; recent ones kept."""
    c = _conn(db_session, "prune-old")
    db_session.add(_msg(c.id, days_ago=120))  # old
    db_session.add(_msg(c.id, days_ago=100))  # old
    db_session.add(_msg(c.id, days_ago=10))   # recent
    db_session.add(_msg(c.id, days_ago=1))    # recent
    db_session.commit()

    result = prune_old_messages(retention_days=90)
    assert result["deleted"] == 2
    assert result["cutoff"] is not None

    # 用独立 session 验证（prune 用独立 SessionLocal）
    from lightmes.database import SessionLocal
    db = SessionLocal()
    try:
        remaining = db.query(MachineMessage).filter(
            MachineMessage.machine_connection_id == c.id
        ).all()
        assert len(remaining) == 2
        # 剩下的应该都是 <= 90 天的
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        for m in remaining:
            assert m.received_at >= cutoff
    finally:
        db.close()


def test_prune_per_connection_keeps_only_recent_n(db_session):
    """Per-connection: keeps only the most recent N messages."""
    c1 = _conn(db_session, "prune-c1")
    c2 = _conn(db_session, "prune-c2")
    # c1: 5 条，保留最新 3 条
    for i in range(5):
        db_session.add(MachineMessage(
            machine_connection_id=c1.id,
            topic=f"t/{i}",
            raw_payload="x",
            received_at=datetime.now(timezone.utc) - timedelta(days=4 - i),
            processing_status="ok",
        ))
    # c2: 2 条，全部保留（< max）
    for i in range(2):
        db_session.add(MachineMessage(
            machine_connection_id=c2.id,
            topic=f"t/{i}",
            raw_payload="x",
            received_at=datetime.now(timezone.utc) - timedelta(days=1 - i),
            processing_status="ok",
        ))
    db_session.commit()

    result = prune_per_connection(max_per_connection=3)
    assert result["deleted"] == 2  # c1 删 2 条，c2 删 0 条

    from lightmes.database import SessionLocal
    db = SessionLocal()
    try:
        c1_msgs = db.query(MachineMessage).filter(
            MachineMessage.machine_connection_id == c1.id
        ).all()
        c2_msgs = db.query(MachineMessage).filter(
            MachineMessage.machine_connection_id == c2.id
        ).all()
        assert len(c1_msgs) == 3
        assert len(c2_msgs) == 2
        # 验证 c1 保留的是最新 3 条（days_ago 0/1/2）
        kept_topics = {m.topic for m in c1_msgs}
        assert kept_topics == {"t/2", "t/3", "t/4"}
    finally:
        db.close()


def test_prune_old_messages_empty_table(db_session):
    """Empty machine_messages → deleted=0, no error."""
    result = prune_old_messages(retention_days=90)
    assert result["deleted"] == 0
    assert result["cutoff"] is not None
