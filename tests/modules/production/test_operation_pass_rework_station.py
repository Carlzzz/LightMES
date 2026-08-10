import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, n_ops=3):
    md = MasterDataService(db_session)
    user = User(username="rwop", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="RWL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="RWP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id])
        for i in range(n_ops)
    ]
    routing = md.create_routing(RoutingCreate(code="RWRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code="RWSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1,
        sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, (ws1, ws2), user, wo


def _force_rework_state(db, su, target_seq, ws):
    """直接设字段模拟返工态（ReworkService.rework 在 Task 6 才支持 expected_repass_station_id）。"""
    su.status = "reworking"
    su.current_operation_seq = target_seq
    su.rework_target_station_id = ws.id
    db.flush()


def test_rework_first_repass_wrong_station_blocked(db_session):
    db, (ws1, ws2), user, wo = _setup(db_session)
    # pass op1 @ ws1, op2 @ ws1
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    # 返工到 op1，预期 re-pass @ ws2
    _force_rework_state(db, su, target_seq=0, ws=ws2)
    assert su.status == "reworking"
    assert su.rework_target_station_id == ws2.id
    # 试图在 ws1 re-pass -> 拒绝
    with pytest.raises(BusinessRuleError, match="该返工件须在【站2】重做"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    # 字段保留（未消费）
    db.refresh(su)
    assert su.rework_target_station_id == ws2.id


def test_rework_first_repass_correct_station_clears_field(db_session):
    db, (ws1, ws2), user, wo = _setup(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    _force_rework_state(db, su, target_seq=0, ws=ws2)
    # 在 ws2 re-pass -> 通过，字段清空
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.rework_target_station_id is None
    assert su.status == "in_process"


def test_subsequent_repass_not_blocked(db_session):
    db, (ws1, ws2), user, wo = _setup(db_session, n_ops=4)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    _force_rework_state(db, su, target_seq=0, ws=ws2)
    # 首次 re-pass @ ws2
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    # 二次 re-pass（下一工序）@ ws1 -> 不卡
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.current_operation_seq == 2
