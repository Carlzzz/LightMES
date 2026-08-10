"""缺陷详情处理路由 service-level 测试。"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User


def _setup_with_defect(db):
    md = MasterDataService(db)
    user = User(username="drop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DRL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="DRW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="DRW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="DRP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="DRRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DRSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DRWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="DRWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="FLAW", name="瑕疵", category="外观", severity="major")
    db.add(dt); db.flush()
    record = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    db.flush()
    return (ws1, ws2), user, su, record


def test_handle_rework_via_service(db_session):
    db = db_session
    (ws1, ws2), user, su, record = _setup_with_defect(db)
    rec = DefectService(db).handle_rework(
        record_id=record.id, handled_by=user.id,
        target_seq=0, expected_repass_station_id=ws2.id)
    db.refresh(su)
    assert rec.handling_status == "rework"
    assert su.status == "reworking"


def test_handle_scrap_via_service(db_session):
    db = db_session
    (ws1, ws2), user, su, record = _setup_with_defect(db)
    rec = DefectService(db).handle_scrap(record_id=record.id, handled_by=user.id, remark="报废")
    db.refresh(su)
    assert rec.handling_status == "scrap"
    assert su.status == "scrapped"


def test_handle_concession_via_service(db_session):
    db = db_session
    (ws1, ws2), user, su, record = _setup_with_defect(db)
    rec = DefectService(db).handle_concession(record_id=record.id, handled_by=user.id, remark="让步")
    db.refresh(su)
    assert rec.handling_status == "concession"
    assert su.status == "in_process"
