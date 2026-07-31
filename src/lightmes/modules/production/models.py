from sqlalchemy import ForeignKey
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
