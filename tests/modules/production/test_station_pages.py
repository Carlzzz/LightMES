import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="st", password="pw12345", display_name="St"))
    db_session.flush()
    client.post("/login", data={"username": "st", "password": "pw12345"})


def _prod(db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="成品", type="finished"))
    ops = [OperationCreate(seq=10, code="OP10", name="工序10", default_work_station_id=ws.id)]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    return ws


def test_station_page_renders(client, db_session):
    ws = _prod(db_session)
    resp = client.get(f"/production/station?work_station_id={ws.id}")
    assert resp.status_code == 200
    assert "工位作业" in resp.text


def test_load_renders_rich_view(client, db_session):
    ws = _prod(db_session)
    _login(client, db_session)
    resp = client.post("/production/station/load",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200
    assert "工序10" in resp.text  # 路径全景含工序名
    assert "工人" in resp.text or "操作员" in resp.text  # 顶部操作员区
    assert "确认过站" in resp.text                        # PASS 按钮


def test_load_unknown_scan_shows_error(client, db_session):
    ws = _prod(db_session)
    _login(client, db_session)
    resp = client.post("/production/station/load",
                       data={"work_station_id": str(ws.id), "scan": "NOPE"})
    assert resp.status_code == 200
    assert "未找到" in resp.text


def test_load_requires_login(client, db_session):
    ws = _prod(db_session)
    resp = client.post("/production/station/load",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 401


def test_pass_first_item_success(client, db_session):
    ws = _prod(db_session)
    _login(client, db_session)
    resp = client.post("/production/station/pass",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200
    assert "已过" in resp.text or "完工" in resp.text


def test_pass_requires_login(client, db_session):
    ws = _prod(db_session)
    resp = client.post("/production/station/pass",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 401
