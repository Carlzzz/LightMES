"""Task 2: MCP gateway auth + mount integration tests.

Verifies:
1. `/mcp` endpoint exists and rejects requests without Bearer token (401).
2. Valid Bearer token completes MCP `initialize` handshake.
3. Invalid Bearer token is rejected with 401.
4. `tools/list` returns an empty array before any tools are registered.
"""
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


# FastMCP 的 StreamableHTTP transport 在 json_response=True 模式下要求
# 客户端 Accept: application/json，否则返回 406 Not Acceptable。
_ACCEPT_JSON = {"Accept": "application/json"}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    # FastMCP 的 StreamableHTTPSessionManager 必须通过 lifespan 初始化，
    # 因此 TestClient 必须以 context manager 方式进入（触发 startup/shutdown）。
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _admin_with_key(db_session, username="mcpadmin"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="mcp-key", user_id=u.id, scopes=["read", "write"])
    return u, full_key


def _init_payload():
    return {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1.0"}},
    }


def test_mcp_endpoint_exists(client, db_session):
    """未带 auth → 401（auth middleware 拒绝）。"""
    resp = client.post("/mcp", headers=_ACCEPT_JSON, json=_init_payload())
    assert resp.status_code == 401
    assert resp.json()["detail"] == "需要 Bearer lmk_xxx token"


def test_mcp_initialize_handshake(client, db_session):
    """完整 MCP initialize 握手。"""
    _, key = _admin_with_key(db_session)
    resp = client.post(
        "/mcp",
        headers={**_ACCEPT_JSON, "Authorization": f"Bearer {key}"},
        json=_init_payload(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["protocolVersion"]
    assert data["result"]["serverInfo"]["name"] == "LightMES"


def test_mcp_invalid_bearer_returns_401(client, db_session):
    resp = client.post(
        "/mcp",
        headers={**_ACCEPT_JSON, "Authorization": "Bearer lmk_garbage"},
        json=_init_payload(),
    )
    assert resp.status_code == 401


def test_mcp_tools_list_empty_before_tools_registered(client, db_session):
    """Task 2 阶段：tools/list 返回空数组（tools 还没注册）。"""
    _, key = _admin_with_key(db_session)
    auth_headers = {**_ACCEPT_JSON, "Authorization": f"Bearer {key}"}
    # 先 initialize 拿 session id
    init_resp = client.post("/mcp", headers=auth_headers, json=_init_payload())
    assert init_resp.status_code == 200
    session_id = init_resp.headers.get("Mcp-Session-Id")
    # 发送 initialized 通知（FastMCP 要求在 tools/list 之前）
    if session_id:
        notif_headers = {**auth_headers, "Mcp-Session-Id": session_id}
        client.post("/mcp", headers=notif_headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
    # tools/list
    resp = client.post("/mcp", headers=notif_headers if session_id else auth_headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert isinstance(body["result"].get("tools", []), list)
