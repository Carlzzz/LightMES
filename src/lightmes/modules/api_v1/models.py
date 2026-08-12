from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class ApiCallLog(Base, TimestampMixin):
    """API 调用日志（选择性审计）"""
    __tablename__ = "api_call_logs"
    __table_args__ = (
        Index("ix_api_call_logs_path", "path"),
        Index("ix_api_call_logs_status_code", "status_code"),
        Index("ix_api_call_logs_trace_id", "trace_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id"), default=None, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(32), default=None)
    client_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(String(500), default=None)
