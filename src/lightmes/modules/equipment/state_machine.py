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

# 手动计划性状态的保护期（秒）：期间机器信号不覆盖手动状态
_MANUAL_HOLD_SECONDS = 15 * 60


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

        # 仲裁：手动设置的计划性状态（CLEANING/MAINTENANCE）有保护期，
        # 保护期内机器信号不得覆盖——否则人工"计划停机"会被设备周期性
        # RUNNING 报文立即冲掉，计划停机记录形同虚设。
        if (current is not None and source == "machine"
                and current.source == "manual"
                and current.state in PLANNED_STATES
                and (at - current.started_at).total_seconds() < _MANUAL_HOLD_SECONDS):
            return current

        if current is not None:
            current.ended_at = at
            current.duration_seconds = max(0, int((at - current.started_at).total_seconds()))
            # 停机归因保护：机器切换只关机器开的停机记录（手动记录保留归因）；
            # 手动切换是人工裁决，关闭全部 open 记录
            self._close_open_downtime(
                work_station_id, at,
                only_source="machine" if source == "machine" else None)

        line_id = self._line_id_for(work_station_id)
        state = WorkstationState(
            work_station_id=work_station_id, state=new_state,
            started_at=at, source=source, metadata_=metadata,
        )
        self.db.add(state)
        self.db.flush()

        if new_state in DOWNTIME_STATES:
            self._open_downtime(work_station_id, new_state, at, line_id, source=source)

        return state

    def _line_id_for(self, work_station_id: int) -> int | None:
        ws = self.db.get(WorkStation, work_station_id)
        return ws.line_id if ws is not None else None

    def _open_downtime(self, work_station_id: int, state: str, at: datetime,
                       line_id: int | None, source: str = "machine") -> None:
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
            source=source,
            notes=f"Auto-recorded from {source} state {state}",
        ))
        self.db.flush()

    def _close_open_downtime(self, work_station_id: int, at: datetime,
                             only_source: str | None = None) -> None:
        """关闭 open 停机。only_source 限定只关某来源的记录（归因保护）。"""
        q = select(ProductionDowntime).where(
            ProductionDowntime.work_station_id == work_station_id,
            ProductionDowntime.ended_at.is_(None),
        )
        if only_source is not None:
            q = q.where(ProductionDowntime.source == only_source)
        open_dt = self.db.execute(
            q.order_by(ProductionDowntime.started_at.desc())
        ).scalars().first()
        if open_dt is not None:
            open_dt.ended_at = at
            total_secs = max(0, int((at - open_dt.started_at).total_seconds()))
            # 秒级精度与 OEE 计算口径一致；分钟字段保留给报表展示
            open_dt.duration_seconds = total_secs
            open_dt.duration_minutes = total_secs // 60

    def _auto_reason_for(self, state: str) -> DowntimeReason:
        code = _AUTO_REASON_BY_STATE.get(state, "AUTO-STOP")
        reason = self.db.execute(
            select(DowntimeReason).where(DowntimeReason.code == code)
        ).scalars().one_or_none()
        if reason is None:
            # seed 缺失时自动补建（is_system），避免状态流因字典缺失中断
            from lightmes.modules.equipment import SYSTEM_DOWNTIME_REASONS
            spec = next(
                (s for s in SYSTEM_DOWNTIME_REASONS if s["code"] == code),
                {"code": code, "name": f"系统自动({code})", "kind": "unplanned"},
            )
            reason = DowntimeReason(
                code=spec["code"], name=spec["name"], kind=spec["kind"],
                is_active=True, is_system=True)
            self.db.add(reason)
            self.db.flush()
        return reason
