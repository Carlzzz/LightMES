from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType, DefectRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User


def _setup_sn(db):
    md = MasterDataService(db)
    user = User(username="dmop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DML", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="DMW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="DMP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1",
                           default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="DMRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DMSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DMWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="DMWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    return su, user, wo


def test_defect_type_persist(db_session):
    db = db_session
    dt = DefectType(code="SCRATCH", name="划伤", category="外观", severity="major")
    db.add(dt); db.flush()
    db.refresh(dt)
    assert dt.id is not None
    assert dt.is_active is True
    assert dt.severity == "major"


def test_defect_record_persist_with_snapshot(db_session):
    db = db_session
    su, user, wo = _setup_sn(db)
    dt = DefectType(code="DENT", name="凹陷", category="外观", severity="critical")
    db.add(dt); db.flush()
    rec = DefectRecord(
        defect_type_id=dt.id, defect_type_code=dt.code, defect_type_name=dt.name,
        severity=dt.severity, serial_unit_id=su.id, work_order_id=wo.id,
        discovered_by=user.id, handling_status="pending")
    db.add(rec); db.flush()
    db.refresh(rec)
    assert rec.id is not None
    assert rec.defect_type_code == "DENT"
    assert rec.severity == "critical"
    assert rec.handling_status == "pending"
    assert rec.discovered_at is not None
