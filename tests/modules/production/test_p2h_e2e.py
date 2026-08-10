"""P2h E2E: skip + rework station hard-block + reselect station.

Service-level E2E (not TestClient) to avoid pre-existing test isolation issues.
Validates the full flow: pass -> skip -> verify; rework -> hard-block -> re-pass.
"""
import pytest
from sqlalchemy import select, select as sa_select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.station_service import StationService
from lightmes.modules.production.models import OperationRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.modules.auth.models import User, Role
from lightmes.shared.errors import BusinessRuleError


def _setup_3op_2ws(db, supervisor=False):
    """3 工序，op1+op2+op3 都 allowed 在 ws1+ws2。"""
    md = MasterDataService(db)
    uname = "e2esv" if supervisor else "e2eop"
    role_name = "supervisor" if supervisor else "operator"
    role = db.execute(sa_select(Role).where(Role.name == role_name)).scalar_one_or_none()
    if role is None:
        role = Role(name=role_name, display_name=role_name, is_system=True)
        db.add(role); db.flush()
    user = User(username=uname, password_hash="x", display_name=uname, role_id=role.id)
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="E2L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="E2W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="E2W2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="E2P", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id])
        for i in range(3)
    ]
    routing = md.create_routing(RoutingCreate(code="E2RT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="E2SR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="E2WO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db, (ws1, ws2), user, wo


def test_e2e_skip_shows_skipped_in_layer1_and_layer2(db_session):
    """pass op1 -> skip op2 -> Layer 1 op2=skipped, Layer 2 contains skipped op2."""
    db, (ws1, ws2), user, wo = _setup_3op_2ws(db_session, supervisor=True)
    # pass op1 @ ws1
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="E2WO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # skip op2 @ ws1
    OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id, reason="跳过 op2"))
    # 验证 operation_record
    rec = db.execute(select(OperationRecord).where(
        OperationRecord.serial_unit_id == su.id,
        OperationRecord.result == "skip")).scalar_one()
    assert rec.remark == "跳过 op2"
    # 验证 seq 推进
    db.refresh(su)
    assert su.current_operation_seq == 2
    # 验证 Layer 1 (operations) 显示 op2 为 skipped
    view = StationService(db).load(su.sn, ws1.id, user.id)
    status_by_seq = {o.seq: o.status for o in view.operations}
    assert status_by_seq[1] == "done"
    assert status_by_seq[2] == "skipped"
    assert status_by_seq[3] == "current"
    # 验证 Layer 2 (station_operations) 含 op2 (ws1 allowed) 且为 skipped
    layer2_seqs = [(o.seq, o.status) for o in view.station_operations]
    assert (2, "skipped") in layer2_seqs


def test_e2e_rework_station_hard_block_wrong_station(db_session):
    """rework expected=ws2 -> re-pass at ws1 blocked -> re-pass at ws2 ok."""
    db, (ws1, ws2), user, wo = _setup_3op_2ws(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="E2WO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 返工 target_seq=0, expected=ws2
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    db.refresh(su)
    assert su.rework_target_station_id == ws2.id
    # re-pass @ ws1 -> 拒绝
    with pytest.raises(BusinessRuleError, match="须在【站2】重做"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    # 字段保留
    db.refresh(su)
    assert su.rework_target_station_id == ws2.id
    # re-pass @ ws2 -> 通过，字段清空
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.rework_target_station_id is None


def test_e2e_rework_reselect_station(db_session):
    """rework expected=ws2 -> re-rework same target_seq expected=ws1 -> re-pass @ ws1 ok."""
    db, (ws1, ws2), user, wo = _setup_3op_2ws(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="E2WO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 第一次返工 target_seq=0, ws2
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    db.refresh(su)
    assert su.rework_target_station_id == ws2.id
    # 重新发起 target_seq=0 (== current), 改选 ws1
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws1.id)
    db.refresh(su)
    assert su.rework_target_station_id == ws1.id
    # re-pass @ ws1 -> 通过
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.rework_target_station_id is None
    assert su.status == "in_process"


def test_e2e_subsequent_repass_not_blocked(db_session):
    """首次 re-pass 后字段清空，后续 re-pass 在其他站不受约束。"""
    db, (ws1, ws2), user, wo = _setup_3op_2ws(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="E2WO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 返工 target_seq=0, expected=ws2 -> re-pass @ ws2
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    # 首次 re-pass 完成（op1），现在 current_seq=1，下一道 op2 可在 ws1 或 ws2
    # re-pass op2 @ ws1 -> 应通过（不再受 rework_target_station_id 约束）
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.current_operation_seq == 2
