import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.station_service import StationService
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, allowed_specs):
    """allowed_specs: [(ws_idx_in_line, is_default)] per op; 单产线 n_ops 个作业站"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    n_ws = max(max(spec[0] for spec in allowed_specs),
               max(spec[1] for spec in allowed_specs)) + 1 if allowed_specs else 1
    wss = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ws)]
    return md, p, line, wss


def _route_and_wo(db_session, allowed_specs_per_op, n_ops):
    md, p, line, wss = _setup(db_session, [s for spec in allowed_specs_per_op for s in spec])
    operations = []
    for i, spec in enumerate(allowed_specs_per_op):
        # spec: list of (ws_idx, is_default) — 一道工序可多站
        allowed_ids = [wss[idx].id for idx, _ in spec]
        default_idx = next(idx for idx, is_def in spec if is_def)
        operations.append(OperationCreate(
            seq=(i+1)*10, code=f"OP{i+1}", name=f"工序{i+1}",
            default_work_station_id=wss[default_idx].id,
            allowed_work_station_ids=allowed_ids))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=operations))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id,
        line_id=line.id, qty=2, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    return md, p, line, wss, wo


def test_pass_allowed_station_passes(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [[(0, True), (1, False)]], 1)
    svc = OperationPassService(db_session)
    # 在 wss[1]（允许的第二站）首件过站 → 通过
    r = svc.pass_operation(OperationPassInput(
        work_station_id=wss[1].id, work_order_code="WO"))
    assert r.sn == "SN00001"


def test_pass_disallowed_station_rejected(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [[(0, True)]], 1)  # OP10 只允许 wss[0]
    svc = OperationPassService(db_session)
    with pytest.raises(BusinessRuleError, match="应在"):
        svc.pass_operation(OperationPassInput(
            work_station_id=wss[1].id, work_order_code="WO"))  # wss[1] 不在 allowed


def test_load_off_station_raises_with_allowed_names(db_session):
    # 2 工序路线：OP10 只允许 wss[0]，OP20 只允许 wss[1]
    md, p, line, wss, wo = _route_and_wo(db_session, [
        [(0, True)],  # OP10 只允许 wss[0]
        [(1, True)],  # OP20 只允许 wss[1]
    ], 2)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=wss[0].id, work_order_code="WO"))
    # 单元现在在 OP20@wss[1]，在 wss[0] 扫 SN → off-station 抛错
    from lightmes.modules.production.repository import SerialUnitRepository
    su = SerialUnitRepository(db_session).get_by_sn(r.sn)
    with pytest.raises(BusinessRuleError, match="应在"):
        StationService(db_session).load(su.sn, wss[0].id, operator_id=None)


def test_next_op_can_continue_here(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [
        [(0, True)],            # OP10 只 wss[0]
        [(0, False), (1, True)]  # OP20 允许 wss[0] 和 wss[1]，默认 wss[1]
    ], 2)
    svc = OperationPassService(db_session)
    # 在 wss[0] 过 OP10 → next_op_can_continue_here=True（OP20 也允许 wss[0]）
    r = svc.pass_operation(OperationPassInput(work_station_id=wss[0].id, work_order_code="WO"))
    assert r.next_op_can_continue_here is True


def test_next_op_cannot_continue_here(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [
        [(0, True)],  # OP10 wss[0]
        [(1, True)],  # OP20 只 wss[1]
    ], 2)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=wss[0].id, work_order_code="WO"))
    assert r.next_op_can_continue_here is False  # OP20 不允许 wss[0]
