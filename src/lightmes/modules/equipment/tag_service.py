from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import SIGNAL_TYPES, MachineTag
from lightmes.modules.equipment.schemas import TagCreate, TagUpdate
from lightmes.shared.errors import NotFoundError, ValidationError


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


class TagService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def apply_transform(self, tag: MachineTag, raw):
        """Apply value_map / scale / offset transform. Never raises."""
        t = tag.transform or {}
        if t.get("value_map"):
            vm = t["value_map"]
            key = "1" if raw is True else ("0" if raw is False else str(raw))
            if key in vm:
                return vm[key]
            if "default" in vm:
                return vm["default"]
            return raw
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and _is_number(raw)):
            value = float(raw)
            if t.get("scale") is not None:
                value *= float(t["scale"])
            if t.get("offset") is not None:
                value += float(t["offset"])
            return value
        return raw

    def list_active_for_topic(self, topic_id: int) -> list[MachineTag]:
        return list(self.db.execute(
            select(MachineTag).where(
                MachineTag.machine_topic_id == topic_id,
                MachineTag.is_active.is_(True),
            )
        ).scalars().all())

    def get(self, tag_id: int) -> MachineTag:
        tag = self.db.get(MachineTag, tag_id)
        if tag is None:
            raise NotFoundError(f"信号标签不存在: {tag_id}")
        return tag

    def create(self, data: TagCreate) -> MachineTag:
        if data.signal_type not in SIGNAL_TYPES:
            raise ValidationError(f"signal_type 必须是 {SIGNAL_TYPES} 之一: {data.signal_type}")
        tag = MachineTag(**data.model_dump())
        self.db.add(tag)
        self.db.flush()
        return tag

    def update(self, tag_id: int, data: TagUpdate) -> MachineTag:
        tag = self.get(tag_id)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(tag, k, v)
        if tag.signal_type not in SIGNAL_TYPES:
            raise ValidationError(f"signal_type 必须是 {SIGNAL_TYPES} 之一")
        self.db.flush()
        return tag

    def delete(self, tag_id: int) -> None:
        tag = self.get(tag_id)
        self.db.delete(tag)
        self.db.flush()
