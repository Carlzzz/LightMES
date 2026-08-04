from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class GenealogyBind(Base, TimestampMixin):
    __tablename__ = "genealogy_binds"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_sn_id: Mapped[int] = mapped_column(
        ForeignKey("serial_units.id"), index=True
    )
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    component_type: Mapped[str] = mapped_column()  # serial/batch
    component_sn: Mapped[str | None] = mapped_column(index=True, default=None)
    component_batch_no: Mapped[str | None] = mapped_column(index=True, default=None)
    qty: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    bind_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    station_pass_id: Mapped[int | None] = mapped_column(
        ForeignKey("station_passes.id"), default=None
    )
    status: Mapped[str] = mapped_column(default="active")  # active/unbound
    unbind_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    unbind_reason: Mapped[str | None] = mapped_column(default=None)
