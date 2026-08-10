import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import OperationRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, n_ops=3):
    md = MasterDataService(db_session)
    user = User(username="skipop", password_hash="x", display_name="主管")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="SKL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SKW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SKP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])
        for i in range(n_ops)
    ]
    routing = md.create_routing(RoutingCreate(code="SKRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code="SKSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SKWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1,
        sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, ws, user, wo


def test_skip_advances_seq_and_writes_skip_record(db_session):
    db, ws, user, wo = _setup(db_session)
    # 先 pass 第一道
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 跳过第二道
    result = OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="临时取消"))
    assert result.skipped_op.seq == 2
    assert result.next_op.seq == 3
    assert result.is_finished is False
    # 验证 skip 记录
    rec = db.execute(select(OperationRecord).where(
        OperationRecord.serial_unit_id == su.id,
        OperationRecord.result == "skip")).scalar_one()
    assert rec.remark == "临时取消"
    # 验证 seq 推进
    db.refresh(su)
    assert su.current_operation_seq == 2


def test_skip_last_op_rejected(db_session):
    db, ws, user, wo = _setup(db_session, n_ops=2)
    # pass 第一道
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 跳过末道（第二道）-> 拒绝
    with pytest.raises(BusinessRuleError, match="末工序不可跳过"):
        OperationPassService(db).skip_operation(OperationSkipInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="试图跳末道"))


def test_skip_publishes_operation_skipped_event(db_session):
    from lightmes.modules.production.events import OperationSkipped
    from lightmes.shared.events import event_bus
    received = []
    event_bus.subscribe(OperationSkipped, lambda e: received.append(e))
    db, ws, user, wo = _setup(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="事件测试"))
    assert len(received) == 1
    assert received[0].reason == "事件测试"
    assert received[0].operation_id is not None
