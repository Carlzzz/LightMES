from sqlalchemy import select

from lightmes.main import app  # noqa: F401  (registers audit listeners)
from lightmes.shared.audit import AuditContext, AuditLog, _audit_context, set_audit_user
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate


def test_core_entity_create_writes_audit_log(db_session):
    MasterDataService(db_session).create_product(
        ProductCreate(code="AUDIT-1", name="Audit", type="component")
    )
    db_session.flush()

    log = db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "Product")
    ).scalars().first()
    assert log is not None
    assert log.action == "created"
    assert log.after_state is not None
    assert "password" not in log.after_state


def test_set_audit_user_updates_context_without_losing_request_metadata():
    token = _audit_context.set(
        AuditContext(user_id=None, ip_address="10.0.0.1", user_agent="pytest-agent")
    )
    try:
        set_audit_user(42)
        context = _audit_context.get()
        assert context.user_id == 42
        assert context.ip_address == "10.0.0.1"
        assert context.user_agent == "pytest-agent"
    finally:
        _audit_context.reset(token)
