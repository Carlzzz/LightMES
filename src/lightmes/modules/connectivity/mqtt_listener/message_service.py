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
    """Persist one received MQTT message with parsing + action execution."""
    from lightmes.modules.connectivity.models import TopicMapping
    from lightmes.modules.connectivity.parser import MqttMessageParser

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

        # 4. 匹配则解析 + 执行 actions
        parsed_data = None
        actions_triggered = None
        processing_status = "skipped"
        processing_error = None

        if matched:
            parser = MqttMessageParser()
            # PostgreSQL TEXT 拒绝 NUL 字节，先 replace 解码再剔除
            raw_payload = payload.decode("utf-8", errors="replace").replace("\x00", "")
            parsed_data = parser.parse(raw_payload, matched.payload_format)

            # 查 active mappings
            mappings = list(
                db.execute(
                    select(TopicMapping)
                    .where(
                        TopicMapping.machine_topic_id == matched.id,
                        TopicMapping.is_active.is_(True),
                    )
                    .order_by(TopicMapping.priority)
                ).scalars().all()
            )

            if mappings:
                from lightmes.modules.connectivity.action_executor import ActionExecutor

                executor = ActionExecutor(db)
                actions_triggered = executor.execute_all(mappings, parsed_data)
                has_error = any(r["status"] == "error" for r in actions_triggered)
                has_ok = any(r["status"] == "ok" for r in actions_triggered)
                # 全部 skipped → skipped；有 ok → ok；仅 error → error
                if has_ok:
                    processing_status = "ok"
                elif has_error:
                    processing_status = "error"
                else:
                    processing_status = "skipped"
                if has_error:
                    processing_error = "; ".join(
                        r.get("message") or ""
                        for r in actions_triggered
                        if r["status"] == "error"
                    )[:500]
            else:
                processing_status = "ok"

        # 5. 入库
        raw_payload = payload.decode("utf-8", errors="replace").replace("\x00", "")
        msg = MachineMessage(
            machine_connection_id=connection_id,
            topic=topic,
            raw_payload=raw_payload,
            matched_topic_id=matched.id if matched else None,
            parsed_data=parsed_data if parsed_data else None,
            actions_triggered=actions_triggered,
            processing_status=processing_status,
            processing_error=processing_error,
            received_at=received_at,
        )
        db.add(msg)
        # 6. 递增计数器（UPDATE，避免 race condition）
        db.execute(
            update(MachineConnection)
            .where(MachineConnection.id == connection_id)
            .values(messages_received=MachineConnection.messages_received + 1)
        )
        db.commit()
        return MessagePersistResult(
            status=processing_status,
            matched_topic_id=matched.id if matched else None,
        )
    except Exception as e:
        db.rollback()
        return MessagePersistResult(status="error", error=str(e))
    finally:
        db.close()
