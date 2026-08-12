from datetime import datetime
from lightmes.modules.auth.models import ApiKey, User
from lightmes.modules.api_v1.models import ApiCallLog


def test_api_key_model_basic_fields(db_session):
    """ApiKey 模型基础字段可持久化。"""
    # 先建一个 User（FK）
    u = User(username="k1", password_hash="x", display_name="K", is_active=True)
    db_session.add(u); db_session.flush()
    k = ApiKey(
        name="ERP Sync",
        key_prefix="lmk_live_abcd",
        key_hash="argon2$...",
        user_id=u.id,
        scopes=["read", "write"],
        is_active=True,
    )
    db_session.add(k); db_session.flush()
    assert k.id is not None
    assert k.scopes == ["read", "write"]
    assert k.is_active is True
    assert k.expires_at is None
    assert k.last_used_at is None


def test_api_call_log_model_basic_fields(db_session):
    """ApiCallLog 模型字段可持久化。"""
    log = ApiCallLog(
        api_key_id=None, user_id=None,
        method="POST", path="/api/v1/work-orders",
        status_code=201, duration_ms=42,
        trace_id="abc12345", client_ip="127.0.0.1",
        error_detail=None,
    )
    db_session.add(log); db_session.flush()
    assert log.id is not None
    assert log.method == "POST"
    assert log.status_code == 201
