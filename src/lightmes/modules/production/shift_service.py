import re
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import Shift
from lightmes.modules.production.schemas import ShiftCreate, ShiftUpdate
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


_HHMM = re.compile(r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$")
_VALID_DAYS = {1, 2, 3, 4, 5, 6, 7}


class ShiftService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _validate(self, data: ShiftCreate | ShiftUpdate, partial: bool = False) -> None:
        for field in ("start_time", "end_time"):
            v = getattr(data, field, None)
            if v is None and partial:
                continue
            if v is None or not _HHMM.match(v):
                raise ValidationError(f"{field} 必须是 HH:MM 格式: {v}")
        dows = getattr(data, "days_of_week", None)
        if dows is not None:
            for d in dows:
                if d not in _VALID_DAYS:
                    raise ValidationError(f"days_of_week 元素必须是 1-7: {d}")

    def create(self, data: ShiftCreate) -> Shift:
        self._validate(data)
        existing = self.db.execute(
            select(Shift).where(Shift.code == data.code)
        ).scalar_one_or_none()
        if existing is not None:
            raise BusinessRuleError(f"班次编码已存在: {data.code}")
        s = Shift(**data.model_dump())
        self.db.add(s)
        self.db.flush()
        return s

    def update(self, shift_id: int, data: ShiftUpdate) -> Shift:
        s = self.db.get(Shift, shift_id)
        if s is None:
            raise NotFoundError(f"班次不存在: {shift_id}")
        self._validate(data, partial=True)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(s, k, v)
        self.db.flush()
        return s

    def delete(self, shift_id: int) -> None:
        s = self.db.get(Shift, shift_id)
        if s is None:
            raise NotFoundError(f"班次不存在: {shift_id}")
        self.db.delete(s)
        self.db.flush()

    def list_all(self) -> list[Shift]:
        return list(self.db.execute(
            select(Shift).order_by(Shift.sort_order, Shift.start_time)
        ).scalars().all())

    def get_active_for_line(self, line_id: int | None) -> list[Shift]:
        """返回该产线适用的激活班次（含全局班次 line_id IS NULL）。"""
        return list(self.db.execute(
            select(Shift).where(
                Shift.is_active.is_(True),
                (Shift.line_id == line_id) | (Shift.line_id.is_(None)),
            ).order_by(Shift.sort_order, Shift.start_time)
        ).scalars().all())

    def is_cross_overnight(self, s: Shift) -> bool:
        return s.end_time < s.start_time

    def current_at(self, line_id: int | None, now: datetime) -> Shift | None:
        """返回当前时间所在的激活班次（考虑跨夜）。"""
        active = self.get_active_for_line(line_id)
        cur_time = now.strftime("%H:%M")
        cur_dow = now.isoweekday()
        for s in active:
            if s.days_of_week is not None and cur_dow not in s.days_of_week:
                continue
            if self.is_cross_overnight(s):
                # 跨夜：start_time <= cur OR cur < end_time
                if cur_time >= s.start_time or cur_time < s.end_time:
                    return s
            else:
                if s.start_time <= cur_time < s.end_time:
                    return s
        return None
