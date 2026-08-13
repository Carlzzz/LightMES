from datetime import datetime, timedelta

from lightmes.shared.audit import AuditLog
from lightmes.shared.audit_service import AuditService


def test_prune_old_audit_logs(db_session):
    old = AuditLog(entity_type="Product", action="created", created_at=datetime.now() - timedelta(days=400))
    new = AuditLog(entity_type="Product", action="created", created_at=datetime.now())
    db_session.add_all([old, new])
    db_session.flush()

    deleted = AuditService(db_session).prune_old(days=365)

    assert deleted == 1
    assert db_session.get(AuditLog, new.id) is not None
