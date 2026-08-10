"""首检接进过站 E2E：service 层模拟完整流程（避免 TestClient DB 隔离问题）。"""
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
    FirstInspectionConfig, FirstInspectionCheckItem, FirstInspectionRecord,
    FirstInspectionState, OperationRecord,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db, with_fi=True):
    md = MasterDataService(db)
    user = User(username="e2efi", password_hash="x", display_name="操作员")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="E2FL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="E2FW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="E2FP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="E2FRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="E2FSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="E2FWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    if with_fi:
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
    return ws, user, wo, None


def test_e2e_fi_passed_then_second_pass_no_retrigger(db_session):
    """首检通过后，同工单同工序第二次过站不再触发首检（state.last_passed_at 已设）。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    # 第一件：过 op1（需首检，提交合格）
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="E2FWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)])))
    # state.last_passed_at 应已设
    state = db.execute(select(FirstInspectionState).where(
        FirstInspectionState.work_order_id == wo.id,
        FirstInspectionState.operation_id == config.operation_id)).scalar_one()
    assert state.last_passed_at is not None


def test_e2e_fi_failed_leaves_no_operation_record(db_session):
    """首检失败 → 无 operation_record 写入（5c 在步骤 6 之前）。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    with pytest.raises(BusinessRuleError, match="首检不合格"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="E2FWO", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)])))
    op_records = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_records) == 0


def test_e2e_no_fi_config_proceeds_normally(db_session):
    """无首检配置的工序不受影响（回归）。"""
    db = db_session
    ws, user, wo, config = _setup(db, with_fi=False)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="E2FWO", operator_id=user.id))
    assert result.sn is not None
    assert result.passed_op.seq == 1
