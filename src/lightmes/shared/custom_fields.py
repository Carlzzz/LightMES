from __future__ import annotations

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.shared.base import Base, TimestampMixin


class CustomFieldDefinition(Base, TimestampMixin):
    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint("entity_type", "key", name="uq_custom_field_entity_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(50), default="text")
    config: Mapped[dict | None] = mapped_column(JSON, default=None)
    required: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)


class CustomFieldService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def definitions(self, entity_type: str) -> list[CustomFieldDefinition]:
        return list(
            self.db.execute(
                select(CustomFieldDefinition)
                .where(
                    CustomFieldDefinition.entity_type == entity_type,
                    CustomFieldDefinition.is_active.is_(True),
                )
                .order_by(CustomFieldDefinition.position, CustomFieldDefinition.id)
            ).scalars().all()
        )

    def client_config(self, entity_type: str) -> list[dict]:
        return [
            {
                "key": d.key,
                "label": d.label,
                "type": d.type,
                "required": d.required,
                "config": d.config or {},
            }
            for d in self.definitions(entity_type)
        ]
