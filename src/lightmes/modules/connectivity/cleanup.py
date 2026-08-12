"""Message retention cleanup — invoked by CLI ``--cleanup`` or scheduled job.

Uses independent SessionLocal to avoid polluting any request-scoped session.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from lightmes import database
from lightmes.modules.connectivity.models import MachineMessage


def prune_old_messages(retention_days: int = 90) -> dict:
    """Delete machine_messages older than ``retention_days`` days.

    Returns ``{"deleted": count, "cutoff": cutoff_date}``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db = database.SessionLocal()
    try:
        result = db.execute(
            delete(MachineMessage).where(MachineMessage.received_at < cutoff)
        )
        deleted = result.rowcount or 0
        db.commit()
        return {"deleted": deleted, "cutoff": cutoff}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def prune_per_connection(max_per_connection: int = 10000) -> dict:
    """Keep only the most recent ``max_per_connection`` messages per connection.

    Deletes older excess rows per ``machine_connection_id``.
    Returns ``{"deleted": count}``.
    """
    db = database.SessionLocal()
    try:
        # 用 ROW_NUMBER 标记每个 connection 内按 received_at desc 的排名，
        # 排名超过 max_per_connection 的全部删除。
        ranking = (
            select(
                MachineMessage.id,
                func.row_number()
                .over(
                    partition_by=MachineMessage.machine_connection_id,
                    order_by=MachineMessage.received_at.desc(),
                )
                .label("rn"),
            )
            .subquery()
        )
        to_delete = select(ranking.c.id).where(ranking.c.rn > max_per_connection)
        result = db.execute(
            delete(MachineMessage).where(MachineMessage.id.in_(to_delete))
        )
        deleted = result.rowcount or 0
        db.commit()
        return {"deleted": deleted}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
