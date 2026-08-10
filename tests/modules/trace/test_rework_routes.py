"""Task 8: rework allowed-stations route + rework POST receives expected_repass_station_id."""
from sqlalchemy import select as sa_select
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import SessionLocal
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.shared.security import hash_password


def _get_or_create_role(db, name, display_name):
    role = db.execute(sa_select(Role).where(Role.name == name)).scalar_one_or_none()
    if role is None:
        role = Role(name=name, display_name=display_name, is_system=True)
        db.add(role); db.flush()
    return role


def _setup_and_pass(db):
    md = MasterDataService(db)
    line = md.create_line(LineCreate(code="RRL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RR1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RR2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="RRP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RRRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(code="RRWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    role = _get_or_create_role(db, "supervisor", "主管")
    user = User(username="rrop", password_hash=hash_password("pass123"),
                display_name="主管", role_id=role.id)
    db.add(user); db.commit()
    db.refresh(user)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RRWO", operator_id=user.id))
    db.commit()
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    return ws1, ws2, user, su


def test_allowed_stations_requires_login(db_session):
    client = TestClient(app)
    resp = client.get("/trace/rework/allowed-stations", params={"sn": "X", "target_seq": 0})
    assert resp.status_code == 401


def test_allowed_stations_returns_select(db_session):
    db = SessionLocal()
    try:
        ws1, ws2, user, su = _setup_and_pass(db)
        client = TestClient(app)
        client.post("/login", data={"username": "rrop", "password": "pass123"})
        resp = client.get("/trace/rework/allowed-stations",
                          params={"sn": su.sn, "target_seq": 0})
    finally:
        db.close()
    assert resp.status_code == 200
    assert "站1" in resp.text
    assert "站2" in resp.text
    assert "expected_repass_station_id" in resp.text


def test_rework_post_receives_expected_station(db_session):
    db = SessionLocal()
    try:
        ws1, ws2, user, su = _setup_and_pass(db)
        client = TestClient(app)
        client.post("/login", data={"username": "rrop", "password": "pass123"})
        resp = client.post("/trace/rework", data={
            "sn": su.sn, "target_seq": 0,
            "expected_repass_station_id": ws2.id, "reason": "测试",
        })
    finally:
        db.close()
    assert resp.status_code == 200
    assert "站2" in resp.text  # 成功提示含选中站名
