from datetime import date, datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lightmes.shared.base import Base, TimestampMixin


class IssueType(Base, TimestampMixin):
    __tablename__ = "issue_types"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'minor', 'major', 'critical')",
            name="ck_issue_types_severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10))
    is_blocking: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'closed')",
            name="ck_issues_status"),
        CheckConstraint(
            "severity IN ('info', 'minor', 'major', 'critical')",
            name="ck_issues_severity"),
        CheckConstraint(
            "source IN ('station_andon', 'defect_linked', 'manual')",
            name="ck_issues_source"),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('use_as_is', 'rework', 'scrap', 'hold')",
            name="ck_issues_disposition"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_type_id: Mapped[int] = mapped_column(
        ForeignKey("issue_types.id"), index=True)
    issue_type: Mapped["IssueType"] = relationship(
        "IssueType", lazy="joined")
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    serial_unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("serial_units.id"), nullable=True, index=True)
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_orders.id"), nullable=True, index=True)
    work_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), nullable=True, index=True)
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("operations.id"), nullable=True)
    defect_id: Mapped[int | None] = mapped_column(
        ForeignKey("defect_records.id"), nullable=True)
    reported_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(15), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    containment_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IssueAction(Base, TimestampMixin):
    __tablename__ = "issue_actions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('corrective', 'preventive', 'containment')",
            name="ck_issue_actions_type"),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'done', 'verified')",
            name="ck_issue_actions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(15))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="open")
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
