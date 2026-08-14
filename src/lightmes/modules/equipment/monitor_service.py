from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.models import MachineConnection
from lightmes.modules.equipment.models import WorkstationState
from lightmes.modules.masterdata.models import WorkStation


class MonitorService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def current_states(self) -> list[dict]:
        """Current open state per workstation (for the monitor board)."""
        rows = self.db.execute(
            select(WorkstationState).where(WorkstationState.ended_at.is_(None))
        ).scalars().all()
        return [
            {"work_station_id": s.work_station_id, "state": s.state,
             "started_at": s.started_at, "source": s.source,
             "metadata": s.metadata_}
            for s in rows
        ]

    def monitor_board(self) -> list[dict]:
        stations = list(self.db.execute(
            select(WorkStation)
            .where(WorkStation.is_active.is_(True))
            .order_by(WorkStation.line_id, WorkStation.seq)
        ).scalars().all())

        open_states = {
            s.work_station_id: s
            for s in self.db.execute(
                select(WorkstationState).where(WorkstationState.ended_at.is_(None))
            ).scalars().all()
        }

        conns = self.db.execute(
            select(MachineConnection).where(MachineConnection.work_station_id.isnot(None))
        ).scalars().all()
        conn_by_ws: dict[int, list] = {}
        for c in conns:
            conn_by_ws.setdefault(c.work_station_id, []).append(c)

        rows = []
        for ws in stations:
            st = open_states.get(ws.id)
            ws_conns = conn_by_ws.get(ws.id, [])
            conn = ws_conns[0] if ws_conns else None
            rows.append({
                "work_station_id": ws.id,
                "code": ws.code,
                "name": ws.name,
                "state": st.state if st else None,
                "state_started_at": st.started_at if st else None,
                "conn_status": conn.status if conn else None,
                "conn_name": conn.name if conn else None,
            })
        return rows
