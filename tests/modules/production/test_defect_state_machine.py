import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup_passed_sn(db):
    md = MasterDataService(db)
    user = User(username="smop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="SML", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SMW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SMP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SMRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="SMSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SMWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SMWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    return db, ws, user, su


def _set_quarantined(su):
    su.status = "quarantined"


def test_pass_operation_rejects_quarantined(db_session):
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id))


def test_skip_operation_rejects_quarantined(db_session):
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).skip_operation(OperationSkipInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="试图跳"))


def test_rework_allows_quarantined(db_session):
    """rework 仅拒 scrapped，quarantined 天然通过。"""
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    su2 = ReworkService(db).rework(
        sn=su.sn, target_seq=0, expected_repass_station_id=ws.id, operator_id=user.id)
    assert su2.status == "reworking"


def test_scrap_allows_quarantined(db_session):
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    su2 = ReworkService(db).scrap(su.sn, reason="隔离后报废")
    assert su2.status == "scrapped"


def test_scrap_allows_finished(db_session):
    """finished 件发现缺陷也能报废。"""
    db, ws, user, su = _setup_passed_sn(db_session)
    # 推进到 finished（2 工序都过站）
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.status == "finished"
    su2 = ReworkService(db).scrap(su.sn, reason="完工后报废")
    assert su2.status == "scrapped"
