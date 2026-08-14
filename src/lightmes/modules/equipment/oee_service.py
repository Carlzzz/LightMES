from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import ProductionDowntime
from lightmes.modules.production.models import DefectRecord, Shift, WorkOrder


def compute_availability(shift_duration_seconds: float,
                         unplanned_downtime_seconds: float) -> float:
    if shift_duration_seconds <= 0:
        return 0.0
    return max(0.0, (shift_duration_seconds - unplanned_downtime_seconds) / shift_duration_seconds)


def compute_quality(produced_qty: int, scrapped_qty: int) -> float:
    if produced_qty <= 0:
        return 0.0
    return max(0.0, (produced_qty - scrapped_qty) / produced_qty)


def compute_oee(availability: float, quality: float) -> float:
    return availability * quality


def _shift_duration_seconds(shift: Shift) -> float:
    def to_secs(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 3600 + int(m) * 60
    start = to_secs(shift.start_time)
    end = to_secs(shift.end_time)
    if end < start:  # cross-midnight
        return (24 * 3600 - start) + end
    return end - start


class OeeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def unplanned_downtime_seconds(self, work_station_id: int,
                                   since: datetime, until: datetime) -> float:
        rows = self.db.execute(
            select(ProductionDowntime).where(
                ProductionDowntime.work_station_id == work_station_id,
                ProductionDowntime.is_planned.is_(False),
                ProductionDowntime.started_at < until,
                (ProductionDowntime.ended_at.is_(None)) |
                (ProductionDowntime.ended_at > since),
            )
        ).scalars().all()
        total = 0.0
        for dt in rows:
            s = dt.started_at if dt.started_at >= since else since
            e = dt.ended_at if (dt.ended_at is not None and dt.ended_at <= until) else until
            if e > s:
                total += (e - s).total_seconds()
        return total

    def availability_for_station(self, work_station_id: int, shift: Shift,
                                 since: datetime, until: datetime) -> float:
        duration = _shift_duration_seconds(shift)
        unplanned = self.unplanned_downtime_seconds(work_station_id, since, until)
        return compute_availability(duration, unplanned)

    def quality_for_work_order(self, work_order_id: int) -> float:
        wo = self.db.get(WorkOrder, work_order_id)
        if wo is None:
            return 0.0
        scrapped = len(self.db.execute(
            select(DefectRecord).where(
                DefectRecord.work_order_id == work_order_id,
                DefectRecord.handling_status == "scrap",
            )
        ).scalars().all())
        return compute_quality(wo.produced_qty, scrapped)
