from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lightmes.shared.base import Base, TimestampMixin


# ── State constants (module-level, matching defect_service's style) ─────────
RUNNING = "RUNNING"
IDLE = "IDLE"
STOPPED = "STOPPED"
FAULT = "FAULT"
SETUP = "SETUP"
WAITING = "WAITING"
CLEANING = "CLEANING"
MAINTENANCE = "MAINTENANCE"

ALL_STATES = [RUNNING, IDLE, STOPPED, FAULT, SETUP, WAITING, CLEANING, MAINTENANCE]
# unplanned availability loss
LOSS_STATES = [STOPPED, FAULT, WAITING]
# scheduled downtime (not an availability loss)
PLANNED_STATES = [CLEANING, MAINTENANCE]
# every state that auto-opens a ProductionDowntime
DOWNTIME_STATES = LOSS_STATES + PLANNED_STATES

SIGNAL_TYPES = ["state", "good_count", "reject_count", "cycle_complete", "telemetry", "alarm"]

_STATE_CK = "state IN ('RUNNING','IDLE','STOPPED','FAULT','SETUP','WAITING','CLEANING','MAINTENANCE')"


class MachineTag(Base, TimestampMixin):
    __tablename__ = "machine_tags"
    __table_args__ = (
        UniqueConstraint("machine_topic_id", "field_path", "signal_type",
                         name="uq_machine_tag_topic_field_signal"),
        CheckConstraint(
            "signal_type IN ('state','good_count','reject_count','cycle_complete','telemetry','alarm')",
            name="ck_machine_tags_signal_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_topic_id: Mapped[int] = mapped_column(
        ForeignKey("machine_topics.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    field_path: Mapped[str] = mapped_column(String(255))
    signal_type: Mapped[str] = mapped_column(String(20))
    data_type: Mapped[str | None] = mapped_column(String(20), default=None)
    transform: Mapped[dict | None] = mapped_column(JSON, default=None)
    unit: Mapped[str | None] = mapped_column(String(20), default=None)
    last_count_value: Mapped[int | None] = mapped_column(Integer, default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class WorkstationState(Base, TimestampMixin):
    __tablename__ = "workstation_states"
    __table_args__ = (
        Index("ix_ws_state_station_started", "work_station_id", "started_at"),
        Index("ix_ws_state_station_ended", "work_station_id", "ended_at"),
        CheckConstraint(_STATE_CK, name="ck_workstation_states_state"),
        CheckConstraint("source IN ('machine','manual')", name="ck_workstation_states_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"), index=True)
    state: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    source: Mapped[str] = mapped_column(String(20), default="machine")
    # SQLAlchemy reserves the attribute name `metadata` (Base.metadata); the DB
    # column is still named "metadata" via the explicit column name below.
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, default=None)


class ProductionDowntime(Base, TimestampMixin):
    __tablename__ = "production_downtimes"
    __table_args__ = (
        Index("ix_downtime_station", "work_station_id"),
        Index("ix_downtime_line", "line_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"), index=True)
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"), index=True)
    downtime_reason_id: Mapped[int | None] = mapped_column(
        ForeignKey("downtime_reasons.id"), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    is_planned: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(default="machine")  # machine / manual

    downtime_reason: Mapped["DowntimeReason | None"] = relationship("DowntimeReason")


class DowntimeReason(Base, TimestampMixin):
    __tablename__ = "downtime_reasons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_downtime_reason_code"),
        CheckConstraint("kind IN ('planned','unplanned')", name="ck_downtime_reason_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_system: Mapped[bool] = mapped_column(default=False)
