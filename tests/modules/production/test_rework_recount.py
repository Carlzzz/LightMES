from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository, WorkOrderRepository
from lightmes.modules.trace.rework_service import ReworkService


def _single_step_line(db_session, qty=5):
    """单工序路线：首站即末站。返工后可原地重过、再次完工。"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RRF", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RRFL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="RRFSW", name="装配站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="RRFR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRFL", name="r", pattern="RR{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RRFWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return p, w, wo


def test_rework_finished_recount_not_double_counted(db_session):
    """返工再完工不重复计数：一个物理 SN 只计一次 produced_qty。

    首次完工 produced_qty=1；将该完工件返工回 0 再重过完工后，
    produced_qty 必须仍是 1（不得因重复 SerialUnitFinished 事件/++ 变成 2），
    工单也不得被错误地提前 completed。修复前该测试应在 produced_qty==1
    断言处失败（实际为 2）。
    """
    p, w, wo = _single_step_line(db_session, qty=5)
    pass_svc = OperationPassService(db_session)
    wo_repo = WorkOrderRepository(db_session)

    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w.id, work_order_code="RRFWO"))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.status == "finished"
    assert wo_repo.get(wo.id).produced_qty == 1

    # 完工件可返工（rework 仅拒 scrapped；target_seq < current_operation_seq）
    reworked = ReworkService(db_session).rework(res.sn, target_seq=0, reason="返修")
    assert reworked.status == "reworking"
    assert reworked.current_operation_seq == 0

    # 重过单工序：再次末站完工
    r2 = pass_svc.pass_operation(OperationPassInput(work_station_id=w.id, sn=res.sn))
    assert r2.is_finished is True
    su2 = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su2.status == "finished"

    # 关键断言：produced_qty 不因返工重过而 +1（修复前此处失败，实际为 2）
    wo_after = wo_repo.get(wo.id)
    assert wo_after.produced_qty == 1
    assert wo_after.status != "completed"  # qty=5，完工 1 件不触发 completed
    assert su2.is_counted is True  # 首次完工即打标，防重复计数
