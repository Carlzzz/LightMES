from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import (
    ALL_STATES, DOWNTIME_STATES, PLANNED_STATES,
    DowntimeReason, ProductionDowntime, WorkstationState,
)
from lightmes.modules.masterdata.models import WorkStation
from lightmes.shared.errors import BusinessRuleError

_AUTO_REASON_BY_STATE = {
    "FAULT": "AUTO-FAULT",
    "STOPPED": "AUTO-STOP",
    "WAITING": "AUTO-WAIT",
    "CLEANING": "AUTO-CLEAN",
    "MAINTENANCE": "AUTO-MAINT",
}


class WorkstationStateMachine:
    def __init__(self, db: Session) -> None:
        self.db = db

    def current(self, work_station_id: int) -> WorkstationState | None:
        return self.db.execute(
            select(WorkstationState)
            .where(WorkstationState.work_station_id == work_station_id,
                   WorkstationState.ended_at.is_(None))
            .order_by(WorkstationState.started_at.desc())
        ).scalars().first()

    def transition(self, work_station_id: int, new_state: str, *,
                   source: str = "machine", metadata: dict | None = None,
                   at: datetime | None = None) -> WorkstationState:
        if new_state not in ALL_STATES:
            raise BusinessRuleError(f"未知设备状态: {new_state}")
        at = at or datetime.now(timezone.utc)

        # 锁当前 open 行，防并发 transition 竞争
        current = self.db.execute(
            select(WorkstationState)
            .where(WorkstationState.work_station_id == work_station_id,
                   WorkstationState.ended_at.is_(None))
            .order_by(WorkstationState.started_at.desc())
            .with_for_update()
        ).scalars().first()

        if current is not None and current.state == new_state:
            if metadata:
                current.metadata_ = {**(current.metadata_ or {}), **metadata}
            return current

        if current is not None:
            current.ended_at = at
            current.duration_seconds = max(0, int((at - current.started_at).total_seconds()))
            self._close_open_downtime(work_station_id, at)

        line_id = self._line_id_for(work_station_id)
        state = WorkstationState(
            work_station_id=work_station_id, state=new_state,
            started_at=at, source=source, metadata_=metadata,
        )
        self.db.add(state)
        self.db.flush()

        if new_state in DOWNTIME_STATES:
            self._open_downtime(work_station_id, new_state, at, line_id)

        return state

    def _line_id_for(self, work_station_id: int) -> int | None:
        ws = self.db.get(WorkStation, work_station_id)
        return ws.line_id if ws is not None else None

    def _open_downtime(self, work_station_id: int, state: str, at: datetime,
                       line_id: int | None) -> None:
        existing = self.db.execute(
            select(ProductionDowntime).where(
                ProductionDowntime.work_station_id == work_station_id,
                ProductionDowntime.ended_at.is_(None),
            )
        ).scalars().first()
        if existing is not None:
            return
        reason = self._auto_reason_for(state)
        self.db.add(ProductionDowntime(
            line_id=line_id,
            work_station_id=work_station_id,
            downtime_reason_id=reason.id,
            started_at=at,
            is_planned=state in PLANNED_STATES,
            notes=f"Auto-recorded from machine state {state}",
        ))
        self.db.flush()

    def _close_open_downtime(self, work_station_id: int, at: datetime) -> None:
        open_dt = self.db.execute(
            select(ProductionDowntime).where(
                ProductionDowntime.work_station_id == work_station_id,
                ProductionDowntime.ended_at.is_(None),
            ).order_by(ProductionDowntime.started_at.desc())
        ).scalars().first()
        if open_dt is not None:
            open_dt.ended_at = at
            open_dt.duration_minutes = max(0, int((at - open_dt.started_at).total_seconds() // 60))

    def _auto_reason_for(self, state: str) -> DowntimeReason:
        code = _AUTO_REASON_BY_STATE.get(state, "AUTO-STOP")
        reason = self.db.execute(
            select(DowntimeReason).where(DowntimeReason.code == code)
        ).scalars().one()
        return reason
