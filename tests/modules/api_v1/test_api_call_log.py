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


def test_api_call_log_records_mcp_write(db_session):
    """MCP write tool calls (POST /mcp) are audited."""
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    from lightmes.modules.auth.models import User, Role
    from lightmes.shared.security import hash_password
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate

    # Randomized suffix so repeated runs do not collide on unique constraints
    # (committed rows persist across runs on the shared dev DB).
    tag = uuid.uuid4().hex[:8]
    # Setup env
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code=f"MCPLP{tag}", name="壳", type="finished"))
    line = md.create_line(LineCreate(code=f"MCPLL{tag}", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code=f"MCPLW{tag}", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code=f"MCPLR{tag}", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code=f"OP1{tag}", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code=f"MCPLRR{tag}", name="r", pattern=f"MCPL{{SEQ:4}}"))

    # Admin user + key (committed to dev DB for middleware visibility)
    from lightmes.database import SessionLocal
    role_db = SessionLocal()
    try:
        role = role_db.query(Role).filter(Role.name == "admin").first()
        if role is None:
            role = Role(name="admin", display_name="Admin")
            role_db.add(role); role_db.flush()
        u = User(username=f"mcplogadm{tag}", password_hash=hash_password("p"),
                 display_name="A", is_active=True, role_id=role.id)
        role_db.add(u); role_db.flush()
        full_key, _ = ApiKeyService(role_db).create(
            name=f"mcplog-key-{tag}", user_id=u.id, scopes=["read", "write"])
        role_db.commit()
    finally:
        role_db.close()

    # FastMCP 的 StreamableHTTPSessionManager 必须通过 lifespan 初始化，
    # 因此 TestClient 必须以 context manager 方式进入（触发 startup/shutdown）。
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            # MCP session handshake + tools/call create_work_order
            headers = {"Authorization": f"Bearer {full_key}", "Accept": "application/json"}
            init = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}},
            })
            session_id = init.headers.get("Mcp-Session-Id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
            })
            client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "create_work_order",
                           "arguments": {"code": f"MCPLWO{tag}", "product_id": p.id,
                                         "routing_id": r.id, "line_id": line.id,
                                         "qty": 10, "sn_rule_id": rule.id, "priority": 5}},
            })
    finally:
        app.dependency_overrides.clear()

    # Verify audit log
    log_db = SessionLocal()
    try:
        logs = log_db.query(ApiCallLog).filter(
            ApiCallLog.path == "/mcp",
            ApiCallLog.method == "POST",
        ).all()
        assert len(logs) >= 1, "MCP POST should be audited"
    finally:
        log_db.close()

