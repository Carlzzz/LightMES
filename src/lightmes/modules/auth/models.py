from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str] = mapped_column()
    display_name: Mapped[str] = mapped_column()
    role: Mapped[str] = mapped_column(default="operator")
    is_active: Mapped[bool] = mapped_column(default=True)
