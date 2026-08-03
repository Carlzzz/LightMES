from sqlalchemy import ForeignKey, JSON, Numeric, UniqueConstraint
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
