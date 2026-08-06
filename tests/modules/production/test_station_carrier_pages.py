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
    AuthService(db_session).create_user(UserCreate(username="sc", password="pw12345", display_name="Sc"))
    db_session.flush()
    client.post("/login", data={"username": "sc", "password": "pw12345"})


def _released_wo(db_session, qty=2, status_release=True):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    if status_release:
        prod.release_work_order(wo.id)
    db_session.flush()
    return ws, wo


def test_select_wo_shows_remaining(client, db_session):
    ws, wo = _released_wo(db_session, qty=2)
    _login(client, db_session)
    resp = client.post("/production/station/select-wo",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200 and ("剩余" in resp.text or "2" in resp.text)


def test_select_wo_created_rejected(client, db_session):
    ws, wo = _released_wo(db_session, qty=2, status_release=False)  # created 未下达
    _login(client, db_session)
    resp = client.post("/production/station/select-wo",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200 and "✗" in resp.text


def test_bind_and_pass_produces_and_shows_remaining(client, db_session):
    ws, wo = _released_wo(db_session, qty=2)
    _login(client, db_session)
    resp = client.post("/production/station/bind-and-pass",
                       data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-1"})
    assert resp.status_code == 200 and ("已投产" in resp.text or "SN00001" in resp.text)


def test_bind_requires_login(client, db_session):
    ws, wo = _released_wo(db_session, qty=2)
    resp = client.post("/production/station/bind-and-pass",
                       data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-1"})
    assert resp.status_code == 401


def test_bind_exhausted_prompts_new_wo(client, db_session):
    ws, wo = _released_wo(db_session, qty=1)
    _login(client, db_session)
    client.post("/production/station/bind-and-pass",
                data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-1"})
    resp = client.post("/production/station/bind-and-pass",
                       data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-2"})
    assert resp.status_code == 200 and ("✗" in resp.text or "全部投产" in resp.text)
