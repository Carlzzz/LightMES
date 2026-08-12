"""Task 5: query_production_status compose tool integration tests.

Verifies the first compose tool:
1. by work_order_code: returns aggregated WO + produced/planned + defects + line.
2. by sn: resolves SN -> WO and attaches serial_unit.
3. not_found: NotFoundError -> MCP tool-level error (isError=True).

Adaptations from brief:
- discovered_by uses real user id (FK NOT NULL constraint on defect_records).
- not_found uses result.isError pattern (Task 3 learnings: tool errors surface
  as MCP tool-level error, not JSON-RPC `error`).
- Uses _mcp_session helper for FastMCP handshake (Mcp-Session-Id required).
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
from lightmes.modules.production.models import SerialUnit, DefectType, DefectRecord
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password

_ACCEPT_JSON = {"Accept": "application/json"}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _env_with_progress(db_session, sn="AGWQS1"):
    """Create WO + 1 SN with defects for compose query testing."""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AGWQSP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AGWQSL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="AGWQSW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="AGWQSR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="AGWQSRR", name="r", pattern="AGWQS{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWQSWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn=sn, work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=2)
    db_session.add(su); db_session.flush()
    wo.produced_qty = 5  # 50% 进度
    db_session.flush()
    # 发现缺陷的用户（FK NOT NULL）
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    discoverer = User(username="qdsc", password_hash=hash_password("p"),
                      display_name="D", is_active=True, role_id=role.id)
    db_session.add(discoverer); db_session.flush()
    # 加 2 条缺陷
    dt = DefectType(code="QSCRATCH", name="刮花", category="外观",
                    severity="minor", is_active=True)
    db_session.add(dt); db_session.flush()
    for _ in range(2):
        db_session.add(DefectRecord(
            defect_type_id=dt.id, defect_type_code=dt.code, defect_type_name=dt.name,
            severity=dt.severity, serial_unit_id=su.id, work_order_id=wo.id,
            operation_id=None, work_station_id=None, position=None,
            discovered_by=discoverer.id, handling_status="pending"))
    db_session.flush()
    return wo, su


def _admin_key(db_session, username="qadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="q-key", user_id=u.id, scopes=["read"])
    return full_key


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


def test_query_production_status_by_wo_code(client, db_session):
    wo, su = _env_with_progress(db_session)
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "query_production_status",
        "arguments": {"work_order_code": "AGWQSWO"},
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["work_order"]["code"] == "AGWQSWO"
    assert payload["produced_qty"] == 5
    assert payload["planned_qty"] == 10
    assert payload["progress_percent"] == 50
    assert len(payload["recent_defects"]) == 2
    assert payload["line"]["code"] == "AGWQSL"


def test_query_production_status_by_sn(client, db_session):
    wo, su = _env_with_progress(db_session, sn="AGWQSN1")
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "query_production_status",
        "arguments": {"sn": "AGWQSN1"},
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["work_order"]["code"] == "AGWQSWO"
    # by-sn 路径附带 serial_unit
    assert payload["serial_unit"] is not None
    assert payload["serial_unit"]["sn"] == "AGWQSN1"


def test_query_production_status_not_found(client, db_session):
    """NotFoundError -> MCP tool-level error (result.isError=True, 文案含"不存在")。"""
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "query_production_status",
        "arguments": {"work_order_code": "NOSUCH"},
    }, _session_headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Task 3 learnings: tool 业务错误走 result.isError 路径，而非 JSON-RPC error
    assert "result" in data
    result = data["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "不存在" in text or "工单" in text
