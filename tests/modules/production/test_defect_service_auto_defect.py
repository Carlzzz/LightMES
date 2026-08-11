import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.schemas import (
    FirstInspectionInput, FirstInspectionCheckResultInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType, DefectRecord, FirstInspectionRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User


def _setup_with_fi(db):
    md = MasterDataService(db)
    user = User(username="adop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="ADL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="ADW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="ADP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="ADRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="ADSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="ADWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    op1 = md.routings.operations_of(routing.id)[0]
    from lightmes.modules.production.models import FirstInspectionConfig, FirstInspectionCheckItem
    config = FirstInspectionConfig(
        operation_id=op1.id, work_station_id=None, name="首检",
        is_enabled=True, trigger_new_order=True,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    item = FirstInspectionCheckItem(
        config_id=config.id, seq=1, name="外观", check_type="boolean", is_mandatory=True)
    db.add(item); db.flush()
    return ws, user, wo, config, item.id


def test_ensure_system_defect_types_idempotent(db_session):
    svc = DefectService(db_session)
    svc.ensure_system_defect_types()
    svc.ensure_system_defect_types()  # 再调一次
    count = db_session.execute(select(DefectType).where(
        DefectType.code == "FIRST_INSPECTION_FAIL")).scalars().all()
    assert len(count) == 1
    assert count[0].severity == "critical"
    assert count[0].category == "质量"
    assert count[0].is_active is True


def test_log_defect_from_inspection_creates_defect(db_session):
    db = db_session
    ws, user, wo, config, item_id = _setup_with_fi(db)
    # 过 op1（提交合格首检，让 SN 进入 in_process 状态——使 log_defect 不会撞
    # 已隔离/已判废规则；同时绕过 5c 硬卡以便单独测试 log_defect_from_inspection）
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="ADWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)])))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 构造一个 failed fi_record（手动创建，模拟 submit_new_inspection 失败结果）
    fi_record = FirstInspectionRecord(
        config_id=config.id, work_order_id=wo.id, operation_id=config.operation_id,
        work_station_id=ws.id, serial_unit_id=su.id, trigger_reason="new_order",
        inspector_id=user.id, status="failed")
    db.add(fi_record); db.flush()
    defect = DefectService(db).log_defect_from_inspection(
        fi_record=fi_record, sn=su.sn, discovered_by=user.id,
        remark="首检不合格（触发：new_order）")
    db.refresh(su)
    assert defect.defect_type_code == "FIRST_INSPECTION_FAIL"
    assert defect.severity == "critical"
    assert defect.remark == "首检不合格（触发：new_order）"
    assert defect.operation_id == config.operation_id
    assert su.status == "quarantined"
