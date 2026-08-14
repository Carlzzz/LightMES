from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import DowntimeReason, ProductionDowntime
from lightmes.shared.errors import NotFoundError


class DowntimeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_for_station(self, work_station_id: int) -> list[ProductionDowntime]:
        return list(self.db.execute(
            select(ProductionDowntime)
            .where(ProductionDowntime.work_station_id == work_station_id)
            .order_by(ProductionDowntime.started_at.desc())
        ).scalars().all())

    def assign_reason(self, downtime_id: int, reason_id: int | None,
                      notes: str | None = None) -> ProductionDowntime:
        dt = self.db.get(ProductionDowntime, downtime_id)
        if dt is None:
            raise NotFoundError(f"停机记录不存在: {downtime_id}")
        if reason_id is not None:
            reason = self.db.get(DowntimeReason, reason_id)
            if reason is None or not reason.is_active:
                raise NotFoundError(f"停机原因不存在或已停用: {reason_id}")
            dt.downtime_reason_id = reason.id
            dt.is_planned = reason.kind == "planned"
        if notes is not None:
            dt.notes = notes
        self.db.flush()
        return dt
