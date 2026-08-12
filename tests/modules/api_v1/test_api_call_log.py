import uuid

import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db, SessionLocal
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.models import ApiCallLog
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _key(db_session, scopes=None, username=None):
    """Create a user + API key, **committed** to the dev DB.

    ApiCallLog middleware writes via its own SessionLocal() and the test reads
    via SessionLocal() too. For the FK constraint (api_call_logs.api_key_id →
    api_keys.id) to pass, the parent rows must be visible to a fresh session —
    meaning they must be actually committed, not just SAVEPOINT-flushed.

    Username is randomized so repeated runs do not collide on the unique
    username constraint (the committed rows persist across runs).
    """
    scopes = scopes or ["read", "write"]
    username = username or f"logadm_{uuid.uuid4().hex[:8]}"
    # Independent session so the commit really lands on the dev DB.
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "admin").first()
        if role is None:
            role = Role(name="admin", display_name="Admin")
            db.add(role); db.flush()
        u = User(username=username, password_hash=hash_password("p"),
                 display_name="L", is_active=True, role_id=role.id)
        db.add(u); db.flush()
        full_key, record = ApiKeyService(db).create(
            name=f"log-key-{username}", user_id=u.id, scopes=scopes)
        db.commit()
        # Refresh the test's db_session cache so it sees the committed rows
        db_session.expire_all()
        # Re-fetch the user via the test session so the test sees the same id
        u = db_session.merge(u)
        return full_key, u
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def test_api_call_log_records_write(client, db_session):
    """写操作（POST）被记录。"""
    key, u = _key(db_session)
    resp = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"}, json={
        "name": "Test Log", "scopes": ["read"]})
    assert resp.status_code == 201
    # 用独立 session 检查 log（避免 db_session 缓存）
    log_db = SessionLocal()
    try:
        logs = log_db.query(ApiCallLog).filter(
            ApiCallLog.method == "POST",
            ApiCallLog.path == "/api/v1/api-keys",
            ApiCallLog.user_id == u.id,
        ).all()
        assert len(logs) >= 1
        assert logs[-1].status_code == 201
        assert logs[-1].user_id == u.id
        assert logs[-1].trace_id is not None
    finally:
        log_db.close()


def test_api_call_log_records_error(client, db_session):
    """失败调用（4xx）被记录，含 error_detail。"""
    key, u = _key(db_session)
    resp = client.get("/api/v1/work-orders/99999",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    log_db = SessionLocal()
    try:
        logs = log_db.query(ApiCallLog).filter(
            ApiCallLog.path == "/api/v1/work-orders/99999",
            ApiCallLog.user_id == u.id,
        ).all()
        assert len(logs) >= 1
        last = logs[-1]
        assert last.status_code == 404
        assert last.error_detail is not None
        assert "工单不存在" in last.error_detail
    finally:
        log_db.close()


def test_api_call_log_skips_successful_get(client, db_session):
    """成功 GET 不被记录。"""
    key, u = _key(db_session)
    # 先调一次 GET（可能被记录或不被记录）
    client.get("/api/v1/work-orders", headers={"Authorization": f"Bearer {key}"})
    log_db = SessionLocal()
    try:
        # 新加一次 GET，前后取 count 差（按 user_id 隔离，避免他测试干扰）
        before = log_db.query(ApiCallLog).filter(
            ApiCallLog.method == "GET",
            ApiCallLog.path == "/api/v1/work-orders",
            ApiCallLog.status_code == 200,
            ApiCallLog.user_id == u.id,
        ).count()
        client.get("/api/v1/work-orders", headers={"Authorization": f"Bearer {key}"})
        log_db.expire_all()
        after = log_db.query(ApiCallLog).filter(
            ApiCallLog.method == "GET",
            ApiCallLog.path == "/api/v1/work-orders",
            ApiCallLog.status_code == 200,
            ApiCallLog.user_id == u.id,
        ).count()
        assert after == before  # 没有 +1，GET 成功不记录
    finally:
        log_db.close()

