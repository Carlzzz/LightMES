import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, ComponentInput, ParamInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import SerialUnit


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="tr", password="pw12345", display_name="Tr"))
    db_session.flush()
    assert client.post("/login", data={"username": "tr", "password": "pw12345"}).status_code == 204


def _passed_sn(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="PF", name="成品", type="finished"))
    c = md.create_product(
        ProductCreate(code="PC", name="主板", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c.id, qty=1)]))
    line = md.create_line(LineCreate(code="PFL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="PSW", name="装配站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="PR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRL", name="r", pattern="P{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PWO", product_id=fin.id, routing_id=r.id, line_id=line.id,
        qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.add(SerialUnit(sn="MB-7", product_id=c.id))
    db_session.flush()
    res = OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=w.id, work_order_code="PWO",
        components=[ComponentInput(component_product_id=c.id, component_sn="MB-7")],
        params=[ParamInput(param_key="torque", param_value="1.5", unit="N·m")]))
    return res.sn


def test_query_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/trace/query")
    assert resp.status_code == 200
    assert "追溯查询" in resp.text


def test_query_forward_genealogy(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.post("/trace/query", data={"query_type": "genealogy", "value": sn})
    assert resp.status_code == 200
    assert "MB-7" in resp.text


def test_query_reverse_where_used(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.post("/trace/query",
        data={"query_type": "where_used_sn", "value": "MB-7"})
    assert resp.status_code == 200
    assert sn in resp.text


def test_query_history_records_and_params(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.post("/trace/query", data={"query_type": "history", "value": sn})
    assert resp.status_code == 200
    assert "装配" in resp.text
    assert "torque" in resp.text
    assert "MB-7" in resp.text


def test_query_params(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.post("/trace/query", data={"query_type": "params", "value": sn})
    assert resp.status_code == 200
    assert "torque" in resp.text
    assert "1.5" in resp.text


def test_api_genealogy_requires_login(client, db_session):
    resp = client.get("/api/trace/genealogy/ANY")
    assert resp.status_code == 401


def test_api_where_used(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.get("/api/trace/where-used", params={"component_sn": "MB-7"})
    assert resp.status_code == 200
    assert any(p["component_ref"] == "MB-7" for p in resp.json())


def test_rework_page_requires_login(client, db_session):
    resp = client.post("/trace/rework",
        data={"sn": "X", "target_seq": 0, "reason": ""})
    assert resp.status_code == 401
