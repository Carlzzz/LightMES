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
