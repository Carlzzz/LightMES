from sqlalchemy import ForeignKey, Index, JSON, Numeric, text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    type: Mapped[str] = mapped_column()  # finished/semi/component/consumable
    spec: Mapped[str | None] = mapped_column(default=None)
    unit: Mapped[str] = mapped_column(default="pcs")
    track_mode: Mapped[str] = mapped_column(default="none")  # serial/batch/none


class Station(Base, TimestampMixin):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    location: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class Routing(Base, TimestampMixin):
    __tablename__ = "routings"
    __table_args__ = (
        Index(
            "uq_active_routing_per_product", "product_id",
            unique=True, postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    version: Mapped[str] = mapped_column(default="1")
    status: Mapped[str] = mapped_column(default="active")  # active/inactive


class RoutingStep(Base, TimestampMixin):
    __tablename__ = "routing_steps"
    __table_args__ = (UniqueConstraint("routing_id", "seq", name="uq_routing_step_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    seq: Mapped[int] = mapped_column()
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    name: Mapped[str] = mapped_column()
    is_mandatory: Mapped[bool] = mapped_column(default=True)
    binding_config: Mapped[dict | None] = mapped_column(JSON, default=None)


class Bom(Base, TimestampMixin):
    __tablename__ = "boms"
    __table_args__ = (
        Index(
            "uq_active_bom_per_product", "product_id",
            unique=True, postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    version: Mapped[str] = mapped_column(default="1")
    status: Mapped[str] = mapped_column(default="active")  # active/inactive


class BomItem(Base, TimestampMixin):
    __tablename__ = "bom_items"
    __table_args__ = (
        UniqueConstraint(
            "bom_id", "component_product_id", name="uq_bom_item_component"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    track_mode: Mapped[str] = mapped_column()  # denormalized from component product


class Line(Base, TimestampMixin):
    __tablename__ = "lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class WorkStation(Base, TimestampMixin):
    __tablename__ = "work_stations"
    __table_args__ = (
        UniqueConstraint("line_id", "seq", name="uq_work_station_line_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    seq: Mapped[int] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class Operation(Base, TimestampMixin):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("routing_id", "seq", name="uq_operation_routing_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    seq: Mapped[int] = mapped_column()
    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    default_work_station_id: Mapped[int] = mapped_column(
        ForeignKey("work_stations.id")
    )
    is_mandatory: Mapped[bool] = mapped_column(default=True)
    # 预留（P2c/P2d 填逻辑，本期仅建列）
    required_skill_id: Mapped[int | None] = mapped_column(default=None)
    required_level: Mapped[int | None] = mapped_column(default=None)
    sop_id: Mapped[int | None] = mapped_column(default=None)
    panels: Mapped[dict | None] = mapped_column(JSON, default=None)
