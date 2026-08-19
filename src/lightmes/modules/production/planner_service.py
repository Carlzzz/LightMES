from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import WorkOrder, ScheduleChangeLog
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ConflictError


_ACTIVE_STATUSES = ("created", "released", "in_process")


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

    def list_recent_changes(self, limit: int = 50) -> list[ScheduleChangeLog]:
        """返回最近的排程变更日志（最新优先）。"""
        return list(self.db.execute(
            select(ScheduleChangeLog).order_by(
                ScheduleChangeLog.id.desc()
            ).limit(limit)
        ).scalars().all())

    def undo_change(self, log_id: int, user_id: int | None) -> ScheduleChangeLog:
        """撤销一条排程变更：把 log.before 写回工单，并写一条 action=undo 的新日志。"""
        log = self.db.get(ScheduleChangeLog, log_id)
        if log is None:
            raise NotFoundError(f"变更日志不存在: {log_id}")
        if log.undone_at is not None:
            raise BusinessRuleError(f"该变更已撤销: {log_id}")
        wo = self.db.get(WorkOrder, log.work_order_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {log.work_order_id}")
        before = log.before or {}
        current = self._snapshot(wo)
        new_line_id = before.get("line_id")
        new_start = self._parse_iso(before.get("planned_start"))
        new_end = self._parse_iso(before.get("planned_end"))
        # 若 before 含时间窗，校验是否与（除自身外的）其他工单冲突
        if new_line_id is not None and new_start is not None and new_end is not None:
            conflict = self.detect_conflict(
                new_line_id, new_start, new_end, exclude_wo_id=wo.id)
            if conflict is not None:
                raise ConflictError(
                    f"撤销失败：原时段 {new_start.isoformat()} ~ {new_end.isoformat()} "
                    f"已被工单 {conflict.code} 占用")
        wo.line_id = new_line_id
        wo.planned_start = new_start
        wo.planned_end = new_end
        self.db.flush()
        # 标记原日志为已撤销，并写一条 action=undo 的新日志
        log.undone_at = datetime.now()
        after = self._snapshot(wo)
        new_log = ScheduleChangeLog(
            work_order_id=wo.id, user_id=user_id, action="undo",
            before=current, after=after, undone_from_log_id=log.id)
        self.db.add(new_log)
        self.db.flush()
        return new_log

    @staticmethod
    def _parse_iso(s: str | None) -> datetime | None:
        if s is None:
            return None
        return datetime.fromisoformat(s)
