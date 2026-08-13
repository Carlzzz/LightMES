"""Task 10: 4 Issue MCP tools integration tests.

Verifies:
1. tools/list 包含 4 个 issue 工具（list_issues / get_issue / create_issue / update_issue_status）。
2. list_issues 在无数据时返回空数组。
3. create_issue + get_issue: 按 type_code 创建并取回（actions 默认空）。
4. update_issue_status 全链：acknowledge → resolve → close 后 status=closed。
5. create_issue 用不存在的 type_code -> MCP tool-level error (isError=True)。
6. read-only scope 调 create_issue -> MCP tool-level error (isError=True, 含 "scope")。

Pattern：沿用 tests/modules/agent_gateway/test_tools_wrappers.py 的 _mcp_session /
_mcp_call 局部辅助函数（FastMCP 2.x 要求 Mcp-Session-Id + notifications/initialized
握手）。未引入 authenticated_mcp_client fixture，以保持与既有 agent_gateway 测试一致。
"""
import json

import pytest
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.auth.models import Role, User
from lightmes.modules.issue.models import IssueType
from lightmes.shared.security import hash_password

_ACCEPT_JSON = {"Accept": "application/json"}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def issue_type_quality(db_session):
    """默认 seed 已有 'quality' type；测试中查或建（不依赖 dev seed）。"""
    from lightmes.modules.issue.repository import IssueTypeRepository

    t = IssueTypeRepository(db_session).get_by_code("quality")
    if t is None:
        t = IssueType(code="quality", name="质量", severity="major")
        db_session.add(t)
        db_session.flush()
    return t


def _admin_with_key(db_session, username="issueadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role)
        db_session.flush()
    u = User(
        username=username, password_hash=hash_password("p"),
        display_name="A", is_active=True, role_id=role.id,
    )
    db_session.add(u)
    db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="issue-key", user_id=u.id, scopes=["read", "write"],
    )
    return u, full_key


def _readonly_key(db_session, username="issuero"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role)
        db_session.flush()
    u = User(
        username=username, password_hash=hash_password("p"),
        display_name="R", is_active=True, role_id=role.id,
    )
    db_session.add(u)
    db_session.flush()
    ro_key, _ = ApiKeyService(db_session).create(
        name="issue-ro-key", user_id=u.id, scopes=["read"],
    )
    return ro_key


def _mcp_session(client, key):
    """初始化 MCP session，返回带 Mcp-Session-Id 的 headers。"""
    init = client.post(
        "/mcp",
        headers={**_ACCEPT_JSON, "Authorization": f"Bearer {key}"},
        json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
    )
    assert init.status_code == 200, init.text
    session_id = init.headers.get("Mcp-Session-Id")
    headers = {**_ACCEPT_JSON, "Authorization": f"Bearer {key}"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })
    return headers


def _mcp_call(client, key, method, params=None, _session_headers=None):
    headers = _session_headers or {
        **_ACCEPT_JSON, "Authorization": f"Bearer {key}",
    }
    return client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0", "id": 1, "method": method,
            "params": params or {},
        },
    )


def _call_tool(client, key, headers, name, args):
    """封装一次 tools/call：解包 result.content[0].text 为 JSON 返回。

    业务错误（scope / NotFound / BusinessRule）由调用方通过返回的 dict 自行检查，
    因 FastMCP 2.x 把 tool 抛出的异常转成 result.isError=True 的 tool-level error，
    而非 JSON-RPC error。返回原始 response 让调用方自行判断 isError。
    """
    resp = _mcp_call(client, key, "tools/call", {
        "name": name, "arguments": args,
    }, _session_headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _payload(resp_json):
    """从正常的 tools/call 响应解出 payload（dict / list）。

    FastMCP 2.x 对返回空 list 的工具不发 TextContent（content=[]），
    改在 structuredContent.result 携带；非空 list / dict 走 content[0].text。
    两条路径在此统一处理。
    """
    result = resp_json["result"]
    if result.get("content"):
        return json.loads(result["content"][0]["text"])
    # 空 list 路径：fall back to structuredContent.result（已是原生 JSON）
    structured = result.get("structuredContent") or {}
    if "result" in structured:
        return structured["result"]
    raise AssertionError(f"unexpected MCP result shape: {result}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mcp_tools_list_contains_4_issue_tools(client, db_session, issue_type_quality):
    """tools/list 包含 4 个 issue 工具。"""
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)
    resp = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 200
    tool_names = {t["name"] for t in resp.json()["result"]["tools"]}
    expected = {"list_issues", "get_issue", "create_issue", "update_issue_status"}
    missing = expected - tool_names
    assert not missing, f"Missing issue tools: {missing}"


def test_list_issues_returns_empty_when_no_data(client, db_session, issue_type_quality):
    """无数据时返回空数组。"""
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)
    data = _call_tool(client, key, headers, "list_issues", {})
    payload = _payload(data)
    assert payload == []


def test_create_and_get_issue(client, db_session, issue_type_quality):
    """create_issue 按 type_code 创建 → get_issue 取回（actions 空）。"""
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)

    created = _payload(_call_tool(
        client, key, headers, "create_issue",
        {"type_code": "quality", "title": "test from mcp"},
    ))
    assert created["status"] == "open"
    assert created["id"] > 0
    issue_id = created["id"]

    got = _payload(_call_tool(
        client, key, headers, "get_issue", {"issue_id": issue_id},
    ))
    assert got["issue"]["id"] == issue_id
    assert got["issue"]["title"] == "test from mcp"
    assert got["issue"]["issue_type_code"] == "quality"
    assert got["issue"]["source"] == "manual"
    assert got["issue"]["is_blocking"] is False  # severity=major → type.is_blocking=False
    assert got["actions"] == []


def test_update_status_lifecycle(client, db_session, issue_type_quality):
    """acknowledge → resolve → close 全链。"""
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)

    issue_id = _payload(_call_tool(
        client, key, headers, "create_issue",
        {"type_code": "quality", "title": "lc"},
    ))["id"]

    acked = _payload(_call_tool(
        client, key, headers, "update_issue_status",
        {"issue_id": issue_id, "action": "acknowledge"},
    ))
    assert acked["status"] == "acknowledged"

    resolved = _payload(_call_tool(
        client, key, headers, "update_issue_status",
        {"issue_id": issue_id, "action": "resolve",
         "root_cause": "rc", "containment_action": "ca", "disposition": "rework"},
    ))
    assert resolved["status"] == "resolved"

    closed = _payload(_call_tool(
        client, key, headers, "update_issue_status",
        {"issue_id": issue_id, "action": "close"},
    ))
    assert closed["status"] == "closed"

    got = _payload(_call_tool(
        client, key, headers, "get_issue", {"issue_id": issue_id},
    ))
    assert got["issue"]["status"] == "closed"


def test_create_issue_with_unknown_type_code_errors(client, db_session, issue_type_quality):
    """create_issue 传不存在的 type_code → MCP tool-level error (isError=True)。"""
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)
    data = _call_tool(
        client, key, headers, "create_issue",
        {"type_code": "no_such_type", "title": "x"},
    )
    assert "result" in data
    assert data["result"].get("isError") is True
    text = data["result"]["content"][0]["text"]
    assert "不存在" in text or "type" in text.lower()


def test_readonly_scope_blocked_on_write(client, db_session, issue_type_quality):
    """read-only key 调 create_issue → MCP tool-level error (含 "scope")。"""
    ro_key = _readonly_key(db_session)
    headers = _mcp_session(client, ro_key)
    data = _call_tool(
        client, key=ro_key, headers=headers,
        name="create_issue",
        args={"type_code": "quality", "title": "ro attempt"},
    )
    assert "result" in data
    assert data["result"].get("isError") is True
    text = data["result"]["content"][0]["text"]
    assert "scope" in text.lower()
