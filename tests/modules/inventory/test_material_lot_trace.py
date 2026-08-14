import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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
from lightmes.modules.production.models import Batch, OperationRecord, SerialUnit
from lightmes.modules.production.repository import (
    OperationRecordRepository,
    SerialUnitRepository,
)
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.service import ProductionService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    auth = AuthService(db_session)
    role = auth.role_repo.get_by_name("admin")
    if role is None:
        role = auth.create_role(
            RoleCreate(name="admin", display_name="admin", description="admin")
        )
    username = "trace_admin"
    auth.create_user(
        UserCreate(
            username=username,
            password="pw12345",
            display_name=username,
            role_id=role.id,
        )
    )
    db_session.flush()
    resp = client.post("/login", data={"username": username, "password": "pw12345"})
    assert resp.status_code == 204


def _setup(db_session, suffix):
    md = MasterDataService(db_session)
    product = md.create_product(
        ProductCreate(
            code=f"TRACE-P{suffix}",
            name="component",
            type="component",
            track_mode="batch",
        )
    )
    line = md.create_line(LineCreate(code=f"TRACE-L{suffix}", name="line"))
    ws = md.create_work_station(
        WorkStationCreate(
            code=f"TRACE-W{suffix}",
            name="ws",
            line_id=line.id,
            seq=1,
        )
    )
    routing = md.create_routing(
        RoutingCreate(
            code=f"TRACE-R{suffix}",
            name="routing",
            product_id=product.id,
            operations=[
                OperationCreate(
                    seq=1,
                    code="OP1",
                    name="op",
                    default_work_station_id=ws.id,
                    allowed_work_station_ids=[ws.id],
                )
            ],
        )
    )
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(
        SnRuleCreate(
            code=f"TRACE-S{suffix}",
            name="sn",
            pattern=f"TR{suffix}{{SEQ:4}}",
        )
    )
    wo = prod.create_work_order(
        WorkOrderCreate(
            code=f"TRACE-WO{suffix}",
            product_id=product.id,
            routing_id=routing.id,
            line_id=line.id,
            qty=1,
            sn_rule_id=rule.id,
        )
    )
    prod.release_work_order(wo.id)
    batch = db_session.execute(
        select(Batch).where(Batch.work_order_id == wo.id)
    ).scalar_one()
    op = md.routings.operations_of(routing.id)[0]
    return product, wo, batch, line, ws, op


def _receive_and_consume(db_session, product, batch, code, quantity, operation_record_id=None):
    svc = MaterialLotService(db_session)
    lot = svc.receive(code=code, product_id=product.id, quantity=10)
    svc.release(lot.code)
    svc.consume(
        batch_id=batch.id,
        operation_record_id=operation_record_id,
        product_id=product.id,
        lot_code=lot.code,
        quantity=quantity,
    )
    db_session.flush()
    return lot


def test_usage_endpoint_returns_material_lot_and_consumption(client, db_session):
    product, wo, batch, *_ = _setup(db_session, "1")
    lot = _receive_and_consume(db_session, product, batch, "TRACE-LOT-1", 3)
    _login(client, db_session)

    resp = client.get(f"/api/inventory/material-lots/{lot.id}/usage")
    assert resp.status_code == 200
    data = resp.json()

    assert data["material_lot"] == {
        "id": lot.id,
        "code": lot.code,
        "product_id": product.id,
    }
    assert len(data["consumptions"]) == 1
    consumption = data["consumptions"][0]
    assert consumption["batch_id"] == batch.id
    assert consumption["operation_record_id"] is None
    assert consumption["quantity"] == 3.0
    assert consumption["created_at"] is not None
    assert data["work_orders"] == [{"id": wo.id, "code": wo.code}]
    auto_su = db_session.execute(
        select(SerialUnit).where(SerialUnit.work_order_id == wo.id)
    ).scalars().all()
    assert len(auto_su) == 1
    assert data["serial_units"] == [
        {"id": auto_su[0].id, "sn": auto_su[0].sn, "work_order_id": wo.id}
    ]


def test_usage_endpoint_derives_batch_serial_unit(client, db_session):
    product, wo, batch, *_ = _setup(db_session, "2")
    su = SerialUnitRepository(db_session).add(
        SerialUnit(
            sn="TRACE-SN-1",
            work_order_id=wo.id,
            product_id=product.id,
            batch_id=batch.id,
        )
    )
    db_session.flush()
    lot = _receive_and_consume(db_session, product, batch, "TRACE-LOT-2", 2)
    _login(client, db_session)

    resp = client.get(f"/api/inventory/material-lots/{lot.id}/usage")
    assert resp.status_code == 200
    data = resp.json()

    auto_su = db_session.execute(
        select(SerialUnit).where(
            SerialUnit.work_order_id == wo.id,
            SerialUnit.id != su.id,
        )
    ).scalars().one()
    assert len(data["serial_units"]) == 2
    assert {u["sn"] for u in data["serial_units"]} == {su.sn, auto_su.sn}
    assert all(u["work_order_id"] == wo.id for u in data["serial_units"])
    assert data["work_orders"] == [{"id": wo.id, "code": wo.code}]


def test_usage_endpoint_derives_serial_unit_from_operation_record(client, db_session):
    product, wo, batch, line, ws, op = _setup(db_session, "3")
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="TRACE-SN-2", work_order_id=wo.id, product_id=product.id)
    )
    rec = OperationRecordRepository(db_session).add(
        OperationRecord(
            serial_unit_id=su.id,
            work_order_id=wo.id,
            operation_id=op.id,
            work_station_id=ws.id,
            line_id=line.id,
            result="pass",
        )
    )
    db_session.flush()
    lot = _receive_and_consume(
        db_session,
        product,
        batch,
        "TRACE-LOT-3",
        4,
        operation_record_id=rec.id,
    )
    _login(client, db_session)

    resp = client.get(f"/api/inventory/material-lots/{lot.id}/usage")
    assert resp.status_code == 200
    data = resp.json()

    assert data["consumptions"][0]["operation_record_id"] == rec.id
    assert len(data["serial_units"]) == 2
    assert su.sn in {u["sn"] for u in data["serial_units"]}
    assert all(u["work_order_id"] == wo.id for u in data["serial_units"])
    assert data["work_orders"] == [{"id": wo.id, "code": wo.code}]


def test_usage_endpoint_unknown_lot_returns_404(client, db_session):
    _login(client, db_session)
    resp = client.get("/api/inventory/material-lots/999999/usage")
    assert resp.status_code == 404
