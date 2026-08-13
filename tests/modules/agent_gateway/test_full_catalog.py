"""Task 7 + Task 10: Full 21-tool catalog verification.

Verifies tools/list returns exactly 21 tools with the expected names:
- 13 thin wrappers (1:1 对应 API v1 endpoints)
- 4 compose tools (query_production_status / list_backlog /
  create_and_schedule_work_order / report_defect_for_sn)
- 4 issue tools (list_issues / get_issue / create_issue / update_issue_status)

Adaptations from brief:
- Fixture uses `with TestClient(app) as c:` (FastMCP StreamableHTTPSessionManager
  requires lifespan startup — bare TestClient(app) yields 500).
- Adds `Accept: application/json` header (FastMCP json_response mode returns 406
  otherwise). The brief's verbatim snippet omits both and would fail at runtime;
  this is the same proven pattern used in tests/modules/agent_gateway/test_auth_and_mount.py.
"""
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password

_ACCEPT_JSON = {"Accept": "application/json"}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _key(db_session, scopes=None, username="catalogadm"):
    scopes = scopes or ["read", "write"]
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="c-key", user_id=u.id, scopes=scopes)
    return full_key


def _mcp_call(client, key, method, params=None, headers=None):
    """单次 MCP 调用。"""
    h = {**_ACCEPT_JSON, "Authorization": f"Bearer {key}"}
    if headers:
        h.update(headers)
    return client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {},
    })


def test_mcp_catalog_has_21_tools(client, db_session):
    """tools/list 列出全部 21 个工具（13 thin + 4 compose + 4 issue）。"""
    key = _key(db_session)
    init = _mcp_call(client, key, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    })
    assert init.status_code == 200, init.text
    session_id = init.headers.get("Mcp-Session-Id")
    headers = {}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    # 发 initialized notification（FastMCP 要求在后续 method 之前）
    client.post("/mcp", headers={
        **_ACCEPT_JSON,
        "Authorization": f"Bearer {key}",
        **headers,
    }, json={
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })
    resp = _mcp_call(client, key, "tools/list", {}, headers=headers)
    assert resp.status_code == 200, resp.text
    tools = resp.json()["result"]["tools"]
    assert len(tools) == 21
    tool_names = {t["name"] for t in tools}
    expected = {
        # 13 thin wrappers
        "list_work_orders", "get_work_order",
        "create_work_order", "patch_work_order_priority",
        "list_serial_units", "get_serial_unit", "get_serial_unit_by_sn",
        "list_defects", "get_defect",
        "list_defect_types",
        "list_api_keys", "create_api_key", "revoke_api_key",
        # 4 compose
        "query_production_status", "list_backlog",
        "create_and_schedule_work_order", "report_defect_for_sn",
        # 4 issue (Task 10)
        "list_issues", "get_issue", "create_issue", "update_issue_status",
    }
    assert tool_names == expected
