from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class SnRule(Base, TimestampMixin):
    __tablename__ = "sn_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), default=None
    )
    pattern: Mapped[str] = mapped_column()
    seq_reset: Mapped[str] = mapped_column(default="never")  # never/daily/monthly
    current_seq: Mapped[int] = mapped_column(default=0)
    seq_period_key: Mapped[str | None] = mapped_column(default=None)


class WorkOrder(Base, TimestampMixin):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    sn_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("sn_rules.id"), default=None
    )
    qty: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")
    source: Mapped[str] = mapped_column(default="manual")
    produced_qty: Mapped[int] = mapped_column(default=0)
    planned_start: Mapped[datetime | None] = mapped_column(default=None)
    planned_end: Mapped[datetime | None] = mapped_column(default=None)


class SerialUnit(Base, TimestampMixin):
    __tablename__ = "serial_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    sn: Mapped[str] = mapped_column(unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(default="in_process")
    current_operation_seq: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=0)
    # 是否已计入工单完工数；返工再完工不重复计数（一个物理 SN 只计一次）
    is_counted: Mapped[bool] = mapped_column(default=False, server_default="false")


class OperationRecord(Base, TimestampMixin):
    __tablename__ = "operation_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    result: Mapped[str] = mapped_column(default="pass")
    remark: Mapped[str | None] = mapped_column(default=None)


class OperationParam(Base, TimestampMixin):
    __tablename__ = "operation_params"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_record_id: Mapped[int] = mapped_column(
        ForeignKey("operation_records.id")
    )
    param_key: Mapped[str] = mapped_column()
    param_value: Mapped[str] = mapped_column()
    unit: Mapped[str | None] = mapped_column(default=None)
    source: Mapped[str] = mapped_column(default="manual")  # manual/auto
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
