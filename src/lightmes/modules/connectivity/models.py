from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from lightmes.shared.base import Base, TimestampMixin


class MachineConnection(Base, TimestampMixin):
    __tablename__ = "machine_connections"
    __table_args__ = (
        UniqueConstraint("name", name="uq_machine_connection_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    protocol: Mapped[str] = mapped_column(String(20), default="mqtt")
    is_active: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    status_message: Mapped[str | None] = mapped_column(String(500), default=None)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    messages_received: Mapped[int] = mapped_column(Integer, default=0)


class MqttConnection(Base, TimestampMixin):
    __tablename__ = "mqtt_connections"
    __table_args__ = (
        UniqueConstraint("machine_connection_id", name="uq_mqtt_per_machine_connection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    broker_host: Mapped[str] = mapped_column(String(255))
    broker_port: Mapped[int] = mapped_column(Integer, default=1883)
    client_id: Mapped[str | None] = mapped_column(String(100), default=None)
    username: Mapped[str | None] = mapped_column(String(100), default=None)
    password_encrypted: Mapped[str | None] = mapped_column(String(500), default=None)
    use_tls: Mapped[bool] = mapped_column(default=False)
    keep_alive_seconds: Mapped[int] = mapped_column(Integer, default=60)
    qos_default: Mapped[int] = mapped_column(Integer, default=0)
    clean_session: Mapped[bool] = mapped_column(default=True)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    reconnect_delay_seconds: Mapped[int] = mapped_column(Integer, default=5)


class MachineTopic(Base, TimestampMixin):
    __tablename__ = "machine_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    topic_pattern: Mapped[str] = mapped_column(String(500))
    payload_format: Mapped[str] = mapped_column(String(20), default="json")
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class MachineMessage(Base):
    """Append-only log — no updated_at."""
    __tablename__ = "machine_messages"
    __table_args__ = (
        Index("ix_machine_messages_conn_received", "machine_connection_id", "received_at"),
        Index("ix_machine_messages_received", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(500))
    raw_payload: Mapped[str] = mapped_column(Text)
    matched_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("machine_topics.id", ondelete="SET NULL"), default=None)
    processing_status: Mapped[str] = mapped_column(String(20), default="ok")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
