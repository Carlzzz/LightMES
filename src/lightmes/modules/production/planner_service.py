from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import WorkOrder, ScheduleChangeLog
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ConflictError


_ACTIVE_STATUSES = ("created", "released", "in_progress")


class PlannerService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_backlog(self, line_id: int | None = None) -> list[WorkOrder]:
        """未排程工单：planned_start IS NULL 或 line_id IS NULL。"""
        q = select(WorkOrder).where(
            WorkOrder.planned_start.is_(None) | WorkOrder.line_id.is_(None)
        )
        if line_id is not None:
            q = q.where(WorkOrder.line_id == line_id)
        q = q.order_by(WorkOrder.priority.desc(), WorkOrder.created_at)
        return list(self.db.execute(q).scalars().all())

    def list_scheduled_in_range(
        self,
        line_ids: list[int],
        start: datetime,
        end: datetime,
    ) -> list[WorkOrder]:
        """时间范围内已排程工单。"""
        return list(self.db.execute(
            select(WorkOrder).where(
                WorkOrder.line_id.in_(line_ids),
                WorkOrder.planned_start.is_not(None),
                WorkOrder.planned_end.is_not(None),
                WorkOrder.planned_start < end,
                WorkOrder.planned_end > start,
            ).order_by(WorkOrder.line_id, WorkOrder.planned_start)
        ).scalars().all())

    def detect_conflict(
        self,
        line_id: int,
        start: datetime,
        end: datetime,
        exclude_wo_id: int | None = None,
    ) -> WorkOrder | None:
        """同产线、状态活跃、时间窗重叠 → 返回冲突 WO。"""
        q = select(WorkOrder).where(
            WorkOrder.line_id == line_id,
            WorkOrder.status.in_(_ACTIVE_STATUSES),
            WorkOrder.planned_start.is_not(None),
            WorkOrder.planned_end.is_not(None),
            WorkOrder.planned_start < end,
            WorkOrder.planned_end > start,
        )
        if exclude_wo_id is not None:
            q = q.where(WorkOrder.id != exclude_wo_id)
        return self.db.execute(q).scalars().first()

    def schedule(
        self,
        wo_id: int,
        line_id: int,
        start: datetime,
        end: datetime,
        user_id: int | None,
        force: bool = False,
    ) -> WorkOrder:
        wo = self.db.get(WorkOrder, wo_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {wo_id}")
        if end <= start:
            raise BusinessRuleError(
                f"planned_end 必须晚于 planned_start: start={start}, end={end}")
        if not force:
            conflict = self.detect_conflict(line_id, start, end, exclude_wo_id=wo.id)
            if conflict is not None:
                raise ConflictError(
                    f"产线 {line_id} 时段 {start.isoformat()} ~ {end.isoformat()} "
                    f"已被工单 {conflict.code} 占用")
        before = self._snapshot(wo)
        wo.line_id = line_id
        wo.planned_start = start
        wo.planned_end = end
        self.db.flush()
        after = self._snapshot(wo)
        self._log_change(wo.id, user_id, "schedule", before, after)
        return wo

    def unschedule(self, wo_id: int, user_id: int | None) -> WorkOrder:
        wo = self.db.get(WorkOrder, wo_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {wo_id}")
        before = self._snapshot(wo)
        wo.planned_start = None
        wo.planned_end = None
        self.db.flush()
        after = self._snapshot(wo)
        self._log_change(wo.id, user_id, "unschedule", before, after)
        return wo

    def _snapshot(self, wo: WorkOrder) -> dict:
        return {
            "line_id": wo.line_id,
            "planned_start": wo.planned_start.isoformat() if wo.planned_start else None,
            "planned_end": wo.planned_end.isoformat() if wo.planned_end else None,
        }

    def _log_change(
        self,
        wo_id: int,
        user_id: int | None,
        action: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        self.db.add(ScheduleChangeLog(
            work_order_id=wo_id, user_id=user_id, action=action,
            before=before, after=after,
        ))
        self.db.flush()
