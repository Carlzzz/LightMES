"""Message persistence logic — invoked by MQTT client task on each received message.

Uses independent SessionLocal to avoid polluting any request-scoped session.
Never raises — failures captured as result.error.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update

from lightmes import database
from lightmes.modules.connectivity.models import (
    MachineConnection,
    MachineMessage,
    MachineTopic,
)
from lightmes.modules.connectivity.topic_match import matches_topic


@dataclass
class MessagePersistResult:
    status: str  # "ok" | "skipped" | "error"
    matched_topic_id: int | None = None
    error: str | None = None


def persist_message(
    connection_id: int,
    topic: str,
    payload: bytes,
    received_at: datetime,
) -> MessagePersistResult:
    """Persist one received MQTT message. Returns result; never raises."""
    db = database.SessionLocal()
    try:
        # 1. 校验 connection 存在
        conn = db.get(MachineConnection, connection_id)
        if conn is None:
            return MessagePersistResult(
                status="error", error=f"connection 不存在: {connection_id}"
            )
        # 2. 查 active topics
        topics = list(
            db.execute(
                select(MachineTopic).where(
                    MachineTopic.machine_connection_id == connection_id,
                    MachineTopic.is_active.is_(True),
                )
            ).scalars().all()
        )
        # 3. 找匹配
        matched = next(
            (t for t in topics if matches_topic(t.topic_pattern, topic)), None
        )
        # 4. 入库
        # PostgreSQL TEXT 拒绝 NUL 字节，先用 replace 解码再剔除
        raw_payload = payload.decode("utf-8", errors="replace").replace("\x00", "")
        msg = MachineMessage(
            machine_connection_id=connection_id,
            topic=topic,
            raw_payload=raw_payload,
            matched_topic_id=matched.id if matched else None,
            processing_status="ok" if matched else "skipped",
            received_at=received_at,
        )
        db.add(msg)
        # 5. 递增计数器（UPDATE，避免 race condition）
        db.execute(
            update(MachineConnection)
            .where(MachineConnection.id == connection_id)
            .values(messages_received=MachineConnection.messages_received + 1)
        )
        db.commit()
        return MessagePersistResult(
            status="ok" if matched else "skipped",
            matched_topic_id=matched.id if matched else None,
        )
    except Exception as e:
        db.rollback()
        return MessagePersistResult(status="error", error=str(e))
    finally:
        db.close()
