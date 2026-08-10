"""缺陷登记页 service-level 测试。"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType, DefectRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User


def _setup(db):
    md = MasterDataService(db)
    user = User(username="dlop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DLL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="DLW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="DLP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="DLRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DLSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DLWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="DLWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="STAIN", name="污渍", category="外观", severity="minor")
    db.add(dt); db.flush()
    return ws, user, su, dt


def test_log_defect_full_flow(db_session):
    db = db_session
    ws, user, su, dt = _setup(db)
    rec = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=user.id,
        position="底面", remark="测试登记")
    db.refresh(su)
    assert su.status == "quarantined"
    assert rec.handling_status == "pending"
    assert rec.position == "底面"
