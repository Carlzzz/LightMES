"""Task 6: list_backlog + create_and_schedule_work_order + report_defect_for_sn.

Verifies the final 3 compose tools:
1. list_backlog: returns BacklogResult with backlog list enriched with product code/name.
2. create_and_schedule_work_order success: creates WO + schedules, returns scheduled=True.
3. create_and_schedule conflict: catches ConflictError, returns scheduled=False + conflict
   dict (per task spec "returns conflict dict, doesn't re-raise"). Adapted from brief: the
   brief's `assert "error" in data` contradicts the task spec — tool surfaces conflict via
   the result payload, not JSON-RPC error.
4. report_defect_for_sn: logs defect + auto-quarantines SN, returns defect_record + status.

Adaptations from brief:
- Uses _mcp_session helper for FastMCP handshake (Task 3+ pattern).
- Conflict test asserts scheduled=False + conflict payload (not JSON-RPC error).
- report_defect_for_sn: DefectRecord.operation_id has FK to operations.id, so we cannot
  pass current_operation_seq (an int seq) directly. Implementation passes operation_id=None.
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
from lightmes.modules.production.models import SerialUnit, DefectType
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
    p = md.create_product(ProductCreate(code="AGWMP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AGWML", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="AGWMW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="AGWMR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="AGWMRR", name="r", pattern="AGWM{SEQ:4}"))
    return p, line, r, rule


def _admin_key(db_session, username="madm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="m-key", user_id=u.id, scopes=["read", "write"])
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


def test_list_backlog(client, db_session):
    p, line, r, rule = _env(db_session)
    ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWMWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "list_backlog", "arguments": {},
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert any(b["code"] == "AGWMWO" for b in payload["backlog"])
    # enriched with product code/name
    item = next(b for b in payload["backlog"] if b["code"] == "AGWMWO")
    assert item["product_code"] == "AGWMP"
    assert item["product_name"] == "壳"


def test_create_and_schedule_work_order(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "create_and_schedule_work_order",
        "arguments": {
            "product_code": "AGWMP", "qty": 50, "line_code": "AGWML",
            "planned_start": "2026-08-20T08:00:00",
            "planned_end": "2026-08-20T16:00:00",
            "priority": 7,
        },
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["scheduled"] is True
    assert payload["work_order"]["code"]  # 自动生成
    assert payload["conflict"] is None


def test_create_and_schedule_conflict(client, db_session):
    """冲突时返回 scheduled=False + conflict dict（不 re-raise）。

    Adapted from brief：brief 期望 JSON-RPC error，但 task spec 明确"catches
    ConflictError → returns conflict dict (doesn't re-raise)"，因此工具走
    成功返回路径，conflict 信息在 result payload 里。
    """
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    # 第一次成功
    resp1 = _mcp_call(client, key, "tools/call", {
        "name": "create_and_schedule_work_order",
        "arguments": {
            "product_code": "AGWMP", "qty": 50, "line_code": "AGWML",
            "planned_start": "2026-08-20T08:00:00",
            "planned_end": "2026-08-20T16:00:00",
        },
    }, _session_headers=headers)
    assert resp1.status_code == 200
    # 第二次同时段冲突
    resp2 = _mcp_call(client, key, "tools/call", {
        "name": "create_and_schedule_work_order",
        "arguments": {
            "product_code": "AGWMP", "qty": 30, "line_code": "AGWML",
            "planned_start": "2026-08-20T10:00:00",
            "planned_end": "2026-08-20T18:00:00",
        },
    }, _session_headers=headers)
    assert resp2.status_code == 200
    payload = json.loads(resp2.json()["result"]["content"][0]["text"])
    assert payload["scheduled"] is False
    assert payload["conflict"] is not None
    # 文案含 "占用" 或 "冲突"
    conflict_text = json.dumps(payload["conflict"], ensure_ascii=False)
    assert "占用" in conflict_text or "冲突" in conflict_text


def test_report_defect_for_sn(client, db_session):
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWMDF", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AGWMDF1", work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    dt = DefectType(code="AGWMSCRATCH", name="刮花", category="外观",
                    severity="minor", is_active=True)
    db_session.add(dt); db_session.flush()
    key = _admin_key(db_session)
    headers = _mcp_session(client, key)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "report_defect_for_sn",
        "arguments": {
            "sn": "AGWMDF1",
            "defect_type_code": "AGWMSCRATCH",
            "remark": "外壳刮花 2cm",
        },
    }, _session_headers=headers)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["defect_record"]["defect_type_code"] == "AGWMSCRATCH"
    assert payload["serial_unit_status"] == "quarantined"
