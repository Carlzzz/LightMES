from sqlalchemy import String, or_, select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.process_snapshot import build_process_snapshot


def backfill_work_order_snapshots(db: Session) -> int:
    missing_snapshot = or_(
        WorkOrder.process_snapshot.is_(None),
        WorkOrder.process_snapshot.cast(String) == "null",
    )
    work_orders = list(
        db.execute(
            select(WorkOrder).where(
                missing_snapshot,
                WorkOrder.status.in_(("released", "in_process")),
            )
        ).scalars().all()
    )
    for wo in work_orders:
        wo.process_snapshot = build_process_snapshot(db, wo)
    db.flush()
    return len(work_orders)
