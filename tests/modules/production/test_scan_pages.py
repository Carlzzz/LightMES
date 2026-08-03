import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="op", password="pw12345", display_name="Op"))
    db_session.flush()
    assert client.post("/login", data={"username": "op", "password": "pw12345"}).status_code == 200


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PGP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="PST1", name="上料"))
    r = md.create_routing(RoutingCreate(code="PRT", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="上料")]))
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
    assert "工位 7" in resp.text


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
