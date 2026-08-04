from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, func, text
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class GenealogyBind(Base, TimestampMixin):
    __tablename__ = "genealogy_binds"
    # DB 层兜底：同一唯一件 SN 最多一条 active 绑定（应用层 SELECT 后 INSERT 有 TOCTOU 竞态）
    __table_args__ = (
        Index(
            "uq_active_component_sn",
            "component_sn",
            unique=True,
            postgresql_where=text("status = 'active' AND component_sn IS NOT NULL"),
        ),
    )

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
