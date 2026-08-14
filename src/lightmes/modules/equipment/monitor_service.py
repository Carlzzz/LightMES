from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import WorkstationState


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
