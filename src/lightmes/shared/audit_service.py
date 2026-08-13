from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from lightmes.shared.audit import AuditLog


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def prune_old(self, *, days: int) -> int:
        cutoff = datetime.now() - timedelta(days=days)
        result = self.db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        self.db.flush()
        return result.rowcount or 0
