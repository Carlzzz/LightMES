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
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.auth.repository import UserRepository


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="ub", password="pw12345", display_name="Ub"))
    db_session.flush()
    client.post("/login", data={"username": "ub", "password": "pw12345"})


def test_unbind_page_renders(client, db_session):
    resp = client.get("/trace/carrier-unbind")
    assert resp.status_code == 200 and "解绑" in resp.text


def test_unbind_submit(client, db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[
            OperationCreate(seq=10, code="OP10", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
            OperationCreate(seq=20, code="OP20", name="工序2", default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=2, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    _login(client, db_session)
    uid = UserRepository(db_session).get_by_username("ub").id
    CarrierService(db_session).bind_first_carrier(wo.id, "PAL-U", uid)
    db_session.flush()
    resp = client.post("/trace/carrier-unbind", data={"scan": "PAL-U"})
    assert resp.status_code == 200 and "✓" in resp.text


def test_unbind_requires_login(client, db_session):
    resp = client.post("/trace/carrier-unbind", data={"scan": "X"})
    assert resp.status_code == 401
