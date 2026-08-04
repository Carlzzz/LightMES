import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, LineCreate, WorkStationCreate,
    RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.models import RoutingStep
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.repository import SerialUnitRepository


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="op", password="pw12345", display_name="Op"))
    db_session.flush()
    assert client.post("/login", data={"username": "op", "password": "pw12345"}).status_code == 204


def _line_2step(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PG2", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PG2L", name="线"))
    s1 = md.create_station(StationCreate(code="PG21", name="上料"))
    s2 = md.create_station(StationCreate(code="PG22", name="装配"))
    w1 = md.create_work_station(WorkStationCreate(
        code="PG21W", name="上料站", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="PG22W", name="装配站", line_id=line.id, seq=2))
    r = md.create_routing(RoutingCreate(code="PG2R", name="路线", product_id=p.id,
        operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
        ]))
    db_session.add_all([
        RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"),
        RoutingStep(routing_id=r.id, seq=2, station_id=s2.id, name="装配"),
    ])
    db_session.flush()
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PG2L", name="r", pattern="P{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PG2W", product_id=p.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return s1, s2, wo


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PGP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PGPL", name="线"))
    s1 = md.create_station(StationCreate(code="PST1", name="上料"))
    w1 = md.create_work_station(WorkStationCreate(
        code="PST1W", name="上料站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="PRT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id)]))
    db_session.add(RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"))
    db_session.flush()
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRL", name="r", pattern="P{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PWO", product_id=p.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return s1


def test_scan_page_requires_login(client, db_session):
    # 未登录 POST 过站页 → 401 + HX-Redirect
    resp = client.post("/production/scan",
        data={"station_id": 1, "code_or_sn": "X"})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"


def test_scan_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/production/scan?station_id=7")
    assert resp.status_code == 200
    assert 'value="7"' in resp.text  # 当前工位回填到表单


def test_scan_first_pass_success_fragment(client, db_session):
    s1 = _line(db_session)
    _login(client, db_session)
    resp = client.post("/production/scan",
        data={"station_id": s1.id, "code_or_sn": "PWO"})
    assert resp.status_code == 200
    assert "P001" in resp.text
    assert "✓" in resp.text


def test_scan_error_fragment_shows_reason(client, db_session):
    s1 = _line(db_session)
    _login(client, db_session)
    resp = client.post("/production/scan",
        data={"station_id": s1.id, "code_or_sn": "NOSUCH"})
    assert resp.status_code == 200
    assert "✗" in resp.text  # 红色错误片段


def test_api_pass_requires_login(client, db_session):
    resp = client.post("/api/production/pass",
        json={"station_id": 1, "work_order_code": "X"})
    assert resp.status_code == 401


def test_api_pass_success(client, db_session):
    s1 = _line(db_session)
    _login(client, db_session)
    resp = client.post("/api/production/pass",
        json={"station_id": s1.id, "work_order_code": "PWO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sn"] == "P001"
    assert body["is_finished"] is True  # 单工序路线，首站即末站


def test_api_pass_maps_domain_error_to_json(client, db_session):
    """API 端点让 DomainError 冒泡到全局 handler → 状态码 + JSON detail。"""
    s1 = _line(db_session)
    _login(client, db_session)
    # 首站过站但工位不符 → BusinessRuleError(422)，全局 handler 映射为 JSON
    resp = client.post("/api/production/pass",
        json={"station_id": s1.id + 999, "work_order_code": "PWO"})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert "应到工位" in body["detail"]


def test_scan_wrong_station_first_pass_rolls_back_orphan(client, db_session):
    """首件扫错工位被拒后，已 flush 的 SerialUnit 必须回滚，不得留下孤儿。"""
    s1, s2, wo = _line_2step(db_session)
    wo_id = wo.id
    wo_code = wo.code
    _login(client, db_session)
    # 首件在错误工位扫工单号 → 生成 SN + SerialUnit(step3) 后防跳站失败(step5)
    resp = client.post("/production/scan",
        data={"station_id": s2.id, "code_or_sn": wo_code})
    assert resp.status_code == 200
    assert "✗" in resp.text  # 错误片段
    # rollback 必须让该工单下的 SerialUnit 数量为 0（没有幻影/孤儿）
    assert SerialUnitRepository(db_session).list_by_work_order(wo_id) == []
