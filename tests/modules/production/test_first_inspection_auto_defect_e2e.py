"""首检 failed 自动建缺陷 E2E：完整流程验证。"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
    FirstInspectionInput, FirstInspectionCheckResultInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem,
    FirstInspectionRecord, DefectRecord, OperationRecord,
)
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup_with_fi(db):
    md = MasterDataService(db)
    user = User(username="ade2", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="AEL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="AEW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="AEP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="AERT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="AESR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="AEWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    op1 = md.routings.operations_of(routing.id)[0]
    config = FirstInspectionConfig(
        operation_id=op1.id, work_station_id=None, name="首检",
        is_enabled=True, trigger_new_order=True,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    db.add(FirstInspectionCheckItem(
        config_id=config.id, seq=1, name="外观", check_type="boolean", is_mandatory=True))
    db.flush()
    return ws, user, wo, config


def _check_item_id(db, config):
    return db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id


def test_e2e_fi_failed_auto_creates_defect_and_quarantines(db_session):
    """首检不合格 → 自动建缺陷 + 隔离 SN + 拒绝过站 + 保留 fi_record。"""
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    item_id = _check_item_id(db, config)
    with pytest.raises(BusinessRuleError, match="首检不合格.*缺陷记录 #"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="AEWO", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)
            ])))
    # SN 被隔离
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    db.refresh(su)
    assert su.status == "quarantined"
    # 缺陷记录创建
    defects = db.execute(select(DefectRecord).where(
        DefectRecord.serial_unit_id == su.id)).scalars().all()
    assert len(defects) == 1
    assert defects[0].defect_type_code == "FIRST_INSPECTION_FAIL"
    assert defects[0].severity == "critical"
    assert "new_order" in defects[0].remark
    # FirstInspectionRecord 保留（status=failed）
    fi_records = db.execute(select(FirstInspectionRecord).where(
        FirstInspectionRecord.serial_unit_id == su.id)).scalars().all()
    assert len(fi_records) == 1
    assert fi_records[0].status == "failed"
    # 无 operation_record（过站未推进）
    op_records = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_records) == 0


def test_e2e_fi_failed_then_repass_blocked_by_quarantine(db_session):
    """首检不合格隔离后，再扫该 SN 过站被拒（已隔离）。"""
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    item_id = _check_item_id(db, config)
    # 第一次：提交不合格首检 → 隔离
    with pytest.raises(BusinessRuleError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="AEWO", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)
            ])))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 第二次：再扫过站 → 被隔离拒绝
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id))


def test_e2e_fi_passed_no_defect(db_session):
    """首检合格 → 无缺陷记录（回归）。"""
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    item_id = _check_item_id(db, config)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="AEWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)
        ])))
    assert result.sn is not None
    su = SerialUnitRepository(db).get_by_sn(result.sn)
    defects = db.execute(select(DefectRecord).where(
        DefectRecord.serial_unit_id == su.id)).scalars().all()
    assert len(defects) == 0
