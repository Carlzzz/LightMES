"""缺陷管理 E2E：登记 → 隔离 → 处理（三路）→ 解除。Service-level。"""
import pytest
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
from lightmes.shared.errors import BusinessRuleError


def _setup(db):
    md = MasterDataService(db)
    user = User(username="e2dm", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="E2DL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="E2DW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="E2DW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="E2DP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="E2DRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="E2DSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="E2DWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="E2DWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="E2SCRATCH", name="划伤", category="外观", severity="major")
    db.add(dt); db.flush()
    return (ws1, ws2), user, su, dt


def test_e2e_log_quarantines_then_pass_blocked(db_session):
    """登记 → 隔离 → 扫码过站被拒。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    db.refresh(su)
    assert su.status == "quarantined"
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))


def test_e2e_concession_unblocks_pass(db_session):
    """登记 → 让步 → 回 in_process → 过站通过。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    record = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_concession(record_id=record.id, handled_by=user.id, remark="让步")
    db.refresh(su)
    assert su.status == "in_process"
    # 过站通过（op2）
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    assert result.passed_op.seq == 2


def test_e2e_scrap_terminal(db_session):
    """登记 → 报废 → 终态 scrapped → 过站拒绝。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    record = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_scrap(record_id=record.id, handled_by=user.id, remark="报废")
    db.refresh(su)
    assert su.status == "scrapped"
    with pytest.raises(BusinessRuleError, match="已scrapped"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))


def test_e2e_rework_then_repass(db_session):
    """登记 → 返工 → reworking → re-pass → in_process。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    record = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_rework(
        record_id=record.id, handled_by=user.id,
        target_seq=0, expected_repass_station_id=ws2.id, remark="返工")
    db.refresh(su)
    assert su.status == "reworking"
    assert su.rework_target_station_id == ws2.id
    # re-pass @ ws2
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    assert result.passed_op.seq == 1
    db.refresh(su)
    assert su.status == "in_process"
    assert su.rework_target_station_id is None  # 首次 re-pass 后清空
