"""Task 3: 验证 station_pass 路由把首检数据传到 pass_operation。
Service-level（直接调 pass_operation 模拟路由聚合后的调用），避免 TestClient DB 隔离问题。
完整 HTMX E2E 在 Task 4。
"""
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
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db):
    md = MasterDataService(db)
    user = User(username="spfi", password_hash="x", display_name="操作员")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="SPFL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SPFW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SPFP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="SPFRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="SPFSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SPFWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
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


def test_route_aggregated_fi_data_reaches_pass_operation(db_session):
    """模拟路由聚合 fi_* 表单后的调用：first_inspection 传到 pass_operation 能创建首检记录。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    # 模拟路由聚合后的 OperationPassInput
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SPFWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)
        ])))
    assert result.sn is not None
    # 验证首检记录已创建（status=passed）
    fi_records = db.execute(select(FirstInspectionRecord).where(
        FirstInspectionRecord.work_order_id == wo.id)).scalars().all()
    assert len(fi_records) == 1
    assert fi_records[0].status == "passed"


def test_route_no_fi_data_blocks_when_needed(db_session):
    """模拟路由未传 first_inspection（操作员没填）：pass_operation 拒绝。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    with pytest.raises(BusinessRuleError, match="该工序需首检"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="SPFWO", operator_id=user.id))
