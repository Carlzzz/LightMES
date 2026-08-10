from sqlalchemy import select

from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem,
)
from lightmes.modules.production.quality_service import FirstInspectionService
from lightmes.modules.production.schemas import (
    FirstInspectionInput, FirstInspectionCheckResultInput,
)


def _setup_with_fi_config(db, check_items_spec):
    """创建工序 + 首检配置 + 检查项 + inspector 用户。返回 (ws, config, wo, inspector_id)。"""
    md = MasterDataService(db)
    line = md.create_line(LineCreate(code="FIL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="FIW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="FIP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1",
                           default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="FIRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="FISR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="FIWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 创建首检配置
    op = md.routings.operations_of(routing.id)[0]
    config = FirstInspectionConfig(
        operation_id=op.id, work_station_id=None, name="首检配置",
        is_enabled=True, trigger_new_order=True,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config)
    db.flush()
    for seq, name, ctype, mand in check_items_spec:
        db.add(FirstInspectionCheckItem(
            config_id=config.id, seq=seq, name=name, check_type=ctype,
            is_mandatory=mand))
    db.flush()
    # 创建 inspector 用户（first_inspection_records.inspector_id 是 FK -> users.id）
    inspector = User(username="fi_inspector", password_hash="x", display_name="质检员")
    db.add(inspector)
    db.flush()
    return ws, config, wo, inspector.id


def test_submit_new_inspection_passes(db_session):
    db = db_session
    ws, config, wo, inspector_id = _setup_with_fi_config(db, [
        (1, "外观", "boolean", True),
    ])
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    fi_svc = FirstInspectionService(db)
    record = fi_svc.submit_new_inspection(
        config=config, work_order_id=wo.id, operation_id=config.operation_id,
        work_station_id=ws.id, inspector_id=inspector_id, trigger_reason="new_order",
        serial_unit_id=None,
        check_results=[FirstInspectionCheckResultInput(
            check_item_id=item_id,
            result_type="boolean", boolean_value=True)])
    assert record.status == "passed"


def test_submit_new_inspection_fails(db_session):
    db = db_session
    ws, config, wo, inspector_id = _setup_with_fi_config(db, [
        (1, "外观", "boolean", True),
    ])
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    fi_svc = FirstInspectionService(db)
    record = fi_svc.submit_new_inspection(
        config=config, work_order_id=wo.id, operation_id=config.operation_id,
        work_station_id=ws.id, inspector_id=inspector_id, trigger_reason="new_order",
        serial_unit_id=None,
        check_results=[FirstInspectionCheckResultInput(
            check_item_id=item_id,
            result_type="boolean", boolean_value=False)])
    assert record.status == "failed"
