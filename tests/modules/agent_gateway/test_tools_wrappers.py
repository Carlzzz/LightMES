"""Task 3 + Task 4: 13 thin wrapper MCP tools integration tests.

Verifies:
1. tools/list 包含 13 个 thin wrapper（4 work_orders / 3 serial_units / 2 defects / 1 defect_types / 3 api_keys）。
2. list_work_orders 返回真实数据。
3. get_serial_unit_by_sn 按业务键查询。
4. create_work_order 在 write scope + admin 角色下成功创建。
5. read-only key 调写工具被 scope 检查拦截（MCP error, 含 "scope"）。
"""
import json

import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password

_ACCEPT_JSON = {"Accept": "application/json"}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _env(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AGWP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AGWL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="AGWW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="AGWR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="AGWRR", name="r", pattern="AGW{SEQ:4}"))
    return p, line, r, rule


def _admin_with_key(db_session, username="wrapadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="wrap-key", user_id=u.id, scopes=["read", "write"])
    return u, full_key


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
    # 发 initialized notification（FastMCP 要求在后续 method 之前）
    client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })
    return headers


def _mcp_call(client, key, method, params=None, _session_headers=None):
    """单次 MCP 调用。tools/call / tools/list 必须先初始化 session。

    用法::

        h = _mcp_session(client, key)
        resp = _mcp_call(client, key, "tools/call", {...}, _session_headers=h)
    """
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


def test_mcp_tools_list_contains_13_thin_wrappers(client, db_session):
    """tools/list 包含 13 个 thin wrapper 工具。"""
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)
    # 列工具
    resp = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    expected = {
        "list_work_orders", "get_work_order",
        "create_work_order", "patch_work_order_priority",
        "list_serial_units", "get_serial_unit", "get_serial_unit_by_sn",
        "list_defects", "get_defect",
        "list_defect_types",
        "list_api_keys", "create_api_key", "revoke_api_key",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


def test_mcp_tool_list_work_orders(client, db_session):
    p, line, r, rule = _env(db_session)
    _, key = _admin_with_key(db_session)
    ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "list_work_orders",
        "arguments": {"page": 1, "size": 20},
    }, _session_headers=headers)
    assert resp.status_code == 200
    # MCP tool 返回结构：result.content 是 list of {type: "text", text: "..."}
    result = resp.json()["result"]
    assert "content" in result
    # 解析 text 内容（FastMCP 默认返回 [TextContent]，text 是 JSON 序列化的结果）
    payload = json.loads(result["content"][0]["text"])
    assert isinstance(payload, list)
    assert any(wo["code"] == "AGWWO" for wo in payload)


def test_mcp_tool_get_serial_unit_by_sn(client, db_session):
    from lightmes.modules.production.models import SerialUnit
    p, line, r, rule = _env(db_session)
    _, key = _admin_with_key(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWSU", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AGWSN1", work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "get_serial_unit_by_sn",
        "arguments": {"sn": "AGWSN1"},
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["sn"] == "AGWSN1"


def test_mcp_tool_create_work_order(client, db_session):
    p, line, r, rule = _env(db_session)
    _, key = _admin_with_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "create_work_order",
        "arguments": {"code": "AGWC1", "product_id": p.id, "routing_id": r.id,
                      "line_id": line.id, "qty": 50, "sn_rule_id": rule.id,
                      "priority": 7},
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["code"] == "AGWC1"
    assert payload["priority"] == 7


def test_mcp_tool_readonly_key_blocked_on_write(client, db_session):
    """read-only key 调写工具 → MCP tool error (isError=True, message 含 "scope")。

    FastMCP 2.x 把 tool 抛出的异常转为 `{"result": {"isError": True,
    "content": [{"type": "text", "text": "..."}]}}`（MCP 规范的 tool-level
    error）；JSON-RPC `error` 字段是协议级错误（unknown method 等），
    scope/role 拒绝属于业务错误故走 isError 路径。
    """
    p, line, r, rule = _env(db_session)
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username="ro_u", password_hash=hash_password("p"),
             display_name="R", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    ro_key, _ = ApiKeyService(db_session).create(
        name="ro-key", user_id=u.id, scopes=["read"])
    headers = _mcp_session(client, ro_key)
    resp = _mcp_call(client, ro_key, "tools/call", {
        "name": "create_work_order",
        "arguments": {"code": "AGWRO", "product_id": p.id, "routing_id": r.id,
                      "line_id": line.id, "qty": 1, "sn_rule_id": rule.id},
    }, _session_headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # MCP tool-level error: result.isError == True, content[0].text 含错误描述
    assert "result" in data
    result = data["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "scope" in text.lower()
