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
    FirstInspectionConfig, FirstInspectionCheckItem, OperationRecord,
)
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup_with_fi(db, check_items_spec, config_enabled=True, trigger_new_order=True):
    md = MasterDataService(db)
    user = User(username="fiop", password_hash="x", display_name="操作员")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="FIL2", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="FIW2", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="FIP2", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="FIRT2", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="FISR2", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="FIWO2", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 给 op1 加首检配置
    op1 = md.routings.operations_of(routing.id)[0]
    config = FirstInspectionConfig(
        operation_id=op1.id, work_station_id=None, name="首检",
        is_enabled=config_enabled, trigger_new_order=trigger_new_order,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    for seq, name, ctype, mand in check_items_spec:
        db.add(FirstInspectionCheckItem(
            config_id=config.id, seq=seq, name=name, check_type=ctype, is_mandatory=mand))
    db.flush()
    return db, ws, user, wo, config, op1


def _check_item_id(db, config):
    return db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id


def test_pass_no_fi_config_skips_gate(db_session):
    """工序无首检配置 → 直接过站（回归）。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.auth.models import User
    md = MasterDataService(db_session)
    user = User(username="nofi", password_hash="x", display_name="op")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="NFL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="NFW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="NFP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="NFRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="NFSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(code="NFWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    result = OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="NFWO", operator_id=user.id))
    assert result.sn is not None  # 无配置 → 直接过站


def test_pass_fi_config_disabled_skips_gate(db_session):
    """config 存在但禁用 → 直接过站。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ], config_enabled=False)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id))
    assert result.sn is not None


def test_pass_fi_needs_but_no_data_blocks(db_session):
    """needs=True + first_inspection=None → 拒绝。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    with pytest.raises(BusinessRuleError, match="该工序需首检"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id))
    # 验证未写过站记录
    op_count = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_count) == 0


def test_pass_fi_needs_passed_data_proceeds(db_session):
    """needs=True + 提交合格首检 → 过站成功。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    item_id = _check_item_id(db, config)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)
        ])))
    assert result.sn is not None
    assert result.passed_op.seq == 1


def test_pass_fi_needs_failed_data_blocks(db_session):
    """needs=True + 提交不合格首检 → 拒绝 + SN 隔离 + 缺陷记录创建。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    item_id = _check_item_id(db, config)
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    with pytest.raises(BusinessRuleError, match="首检不合格.*缺陷记录 #"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)
            ])))
    # 验证未写过站记录（5c 在步骤 6 之前）
    op_count = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_count) == 0
    # 新：SN 被隔离 + 缺陷记录创建
    db.refresh(su)
    assert su.status == "quarantined"
    from lightmes.modules.production.models import DefectRecord
    defects = db.execute(select(DefectRecord).where(
        DefectRecord.serial_unit_id == su.id)).scalars().all()
    assert len(defects) == 1
    assert defects[0].defect_type_code == "FIRST_INSPECTION_FAIL"


def test_skip_operation_does_not_trigger_fi(db_session):
    """跳站不触发首检（回归）。"""
    from lightmes.modules.production.schemas import OperationSkipInput
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    # 跳过 op1（需 supervisor，但 service 层不卡角色，路由层卡）
    result = OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id,
        reason="跳过"))
    assert result.skipped_op.seq == 1
