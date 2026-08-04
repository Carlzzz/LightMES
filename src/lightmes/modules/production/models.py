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
    current_step_seq: Mapped[int] = mapped_column(default=0)
    current_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id"), default=None
    )
    version: Mapped[int] = mapped_column(default=0)


class StationPass(Base, TimestampMixin):
    __tablename__ = "station_passes"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    routing_step_id: Mapped[int] = mapped_column(ForeignKey("routing_steps.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    pass_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    result: Mapped[str] = mapped_column(default="pass")
    remark: Mapped[str | None] = mapped_column(default=None)
