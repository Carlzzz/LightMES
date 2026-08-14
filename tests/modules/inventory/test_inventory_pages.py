import pytest
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth.schemas import RoleCreate, UserCreate
from lightmes.modules.auth.service import AuthService
from lightmes.modules.masterdata.schemas import (
    LineCreate,
    OperationCreate,
    ProductCreate,
    RoutingCreate,
    WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.service import ProductionService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session, role="admin"):
    auth = AuthService(db_session)
    role_row = auth.role_repo.get_by_name(role)
    if role_row is None:
        role_row = auth.create_role(RoleCreate(
            name=role, display_name=role, description=role))
    username = f"inv_{role}"
    auth.create_user(
        UserCreate(username=username, password="pw12345", display_name=username, role_id=role_row.id))
    db_session.flush()
    resp = client.post("/login", data={"username": username, "password": "pw12345"})
    assert resp.status_code == 204
    return username


def _component_product(db_session, code="INV-P"):
    product = MasterDataService(db_session).create_product(
        ProductCreate(code=code, name="组件", type="component", track_mode="batch"))
    db_session.flush()
    return product


def _released_work_order(db_session, suffix="A"):
    md = MasterDataService(db_session)
    product = md.create_product(
        ProductCreate(code=f"INVP{suffix}", name="产品", type="finished"))
    line = md.create_line(LineCreate(code=f"INVL{suffix}", name="产线"))
    ws = md.create_work_station(WorkStationCreate(
        code=f"INVW{suffix}", name="站点", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(
        code=f"INVR{suffix}", name="工艺", product_id=product.id,
        operations=[OperationCreate(
            seq=1, code="OP1", name="工序1",
            default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code=f"INVS{suffix}", name="sn", pattern=f"INV{suffix}{{SEQ:4}}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code=f"INVW{suffix}", product_id=product.id, routing_id=routing.id,
        line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    return wo


def test_stock_movements_page_shows_lot_code(client, db_session):
    product = _component_product(db_session, code="INV-MOV")
    lot = MaterialLotService(db_session).receive(
        code="LOT-MOV", product_id=product.id, quantity=10)
    db_session.flush()
    _login(client, db_session)

    resp = client.get("/inventory/stock-movements")
    assert resp.status_code == 200
    assert lot.code in resp.text


def test_material_lot_detail_page_shows_lot_code(client, db_session):
    product = _component_product(db_session, code="INV-DET")
    lot = MaterialLotService(db_session).receive(
        code="LOT-DET", product_id=product.id, quantity=5)
    db_session.flush()
    _login(client, db_session)

    resp = client.get(f"/inventory/material-lots/{lot.id}")
    assert resp.status_code == 200
    assert lot.code in resp.text


def test_batches_page_shows_batch(client, db_session):
    _released_work_order(db_session, suffix="B")
    _login(client, db_session)

    resp = client.get("/production/batches")
    assert resp.status_code == 200
    assert "批次" in resp.text


def test_stock_movements_json_endpoint(client, db_session):
    product = _component_product(db_session, code="INV-API")
    lot = MaterialLotService(db_session).receive(
        code="LOT-API", product_id=product.id, quantity=3)
    db_session.flush()
    _login(client, db_session)

    resp = client.get("/api/inventory/stock-movements")
    assert resp.status_code == 200
    data = resp.json()
    assert any(item["lot_code"] == lot.code for item in data)


def test_batches_json_endpoint(client, db_session):
    wo = _released_work_order(db_session, suffix="C")
    _login(client, db_session)

    resp = client.get("/api/production/batches")
    assert resp.status_code == 200
    data = resp.json()
    assert any(item["work_order_code"] == wo.code for item in data)
