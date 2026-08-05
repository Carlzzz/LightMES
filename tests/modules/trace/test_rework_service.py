import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, ComponentInput, ParamInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.modules.trace.trace_service import TraceService
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.shared.errors import NotFoundError, BusinessRuleError, ValidationError


def _two_step_line(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="RF", name="成品", type="finished"))
    comp = md.create_product(
        ProductCreate(code="RC", name="螺丝", type="consumable", track_mode="batch"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=comp.id, qty=4)]))
    line = md.create_line(LineCreate(code="RFL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(
        code="RS1W", name="上料站", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="RS2W", name="装配站", line_id=line.id, seq=2))
    r = md.create_routing(RoutingCreate(code="RR", name="路线", product_id=fin.id,
        operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRL", name="r", pattern="R{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWO", product_id=fin.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, comp, w1, w2, wo


def test_rework_rolls_back_step_and_status(db_session):
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="RWO"))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_operation_seq == 1
    reworked = ReworkService(db_session).rework(res.sn, target_seq=0, reason="上料错误")
    assert reworked.status == "reworking"
    assert reworked.current_operation_seq == 0


def test_rework_unbinds_components(db_session):
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(
        work_station_id=w1.id, work_order_code="RWO",
        components=[ComponentInput(component_product_id=comp.id,
                                   component_batch_no="LOT-1", qty=4)]))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    bind = GenealogyBindRepository(db_session).list_active_by_parent(su.id)[0]
    ReworkService(db_session).rework(res.sn, target_seq=0,
                                     unbind_bind_ids=[bind.id], reason="换料")
    assert GenealogyBindRepository(db_session).list_active_by_parent(su.id) == []


def test_rework_then_repass_resets_in_process(db_session):
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="RWO"))
    ReworkService(db_session).rework(res.sn, target_seq=0)
    # 重新过首站：reworking → in_process
    r2 = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, sn=res.sn))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.status == "in_process"
    assert su.current_operation_seq == 1


def test_rework_target_seq_must_be_less(db_session):
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="RWO"))
    with pytest.raises(ValidationError):
        ReworkService(db_session).rework(res.sn, target_seq=5)  # >= current


def test_scrap_terminal(db_session):
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="RWO"))
    scrapped = ReworkService(db_session).scrap(res.sn, reason="报废")
    assert scrapped.status == "scrapped"
    # scrapped 后不可过站
    with pytest.raises(BusinessRuleError):
        pass_svc.pass_operation(OperationPassInput(work_station_id=w2.id, sn=res.sn))


def _three_step_line(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="RF3", name="成品", type="finished"))
    line = md.create_line(LineCreate(code="RF3L", name="线"))
    w1 = md.create_work_station(WorkStationCreate(
        code="RS31W", name="上料站", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="RS32W", name="装配站", line_id=line.id, seq=2))
    w3 = md.create_work_station(WorkStationCreate(
        code="RS33W", name="测试站", line_id=line.id, seq=3))
    r = md.create_routing(RoutingCreate(code="RR3", name="路线", product_id=fin.id,
        operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
            OperationCreate(seq=3, code="OP3", name="测试", default_work_station_id=w3.id),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRL3", name="r", pattern="R3{SEQ:2}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWO3", product_id=fin.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, w1, w2, w3, wo


def test_rework_history_accumulates_records_and_params(db_session):
    """返工重过后履历/工艺参数必须累加：旧 operation_record 保留，重过追加新记录。

    该用例同时验证 TraceService.history_of 读 operation_record/operation_param，
    与 Task 6 放宽的 len(h.passes)==0 无回归（履历非空且随重过增长）。
    """
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(
        work_station_id=w1.id, work_order_code="RWO",
        params=[ParamInput(param_key="torque", param_value="1.5", unit="N·m")]))
    ReworkService(db_session).rework(res.sn, target_seq=0, reason="返修")
    pass_svc.pass_operation(OperationPassInput(
        work_station_id=w1.id, sn=res.sn,
        params=[ParamInput(param_key="torque", param_value="1.8", unit="N·m")]))

    h = TraceService(db_session).history_of(res.sn)
    assert len(h.records) == 2
    assert len(h.params) == 2
    values = {p.param_value for p in h.params}
    assert values == {"1.5", "1.8"}
    # 两次重过均为同一工序（重过同一 OP1）
    op_ids = {r.operation_id for r in h.records}
    assert len(op_ids) == 1
    assert all(r.result == "pass" for r in h.records)


def test_rework_unknown_sn(db_session):
    fin, comp, w1, w2, wo = _two_step_line(db_session)
    with pytest.raises(NotFoundError):
        ReworkService(db_session).rework("NOPE", target_seq=0)


def test_rework_then_multistep_repass_all_steps(db_session):
    """回归：3 步路线全部过完后返工回退，再连续重新过 1→2→3 每一步都应成功。

    旧守卫 `status != "reworking"` 只豁免返工后的首次重过：第 2 次重过时 SN 状态
    已被复位为 in_process，命中原始运行的 pass 记录，误报"该工序已过站"。§5.4 要求
    旧 pass 记录"保留但不阻挡"，故需无条件放行（期望下一工序的 seq>current_operation_seq
    选择逻辑本身就是防重复机制）。
    """
    fin, w1, w2, w3, wo = _three_step_line(db_session)
    pass_svc = OperationPassService(db_session)
    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="RWO3"))
    r2 = pass_svc.pass_operation(OperationPassInput(work_station_id=w2.id, sn=res.sn))
    r3 = pass_svc.pass_operation(OperationPassInput(work_station_id=w3.id, sn=res.sn))
    assert r3.passed_op.seq == 3
    assert r3.is_finished is True
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_operation_seq == 3
    assert su.status == "finished"

    # 完工件可返工（rework 仅拒 scrapped，且 target_seq < current_operation_seq）
    reworked = ReworkService(db_session).rework(res.sn, target_seq=0, reason="返修")
    assert reworked.status == "reworking"
    assert reworked.current_operation_seq == 0

    # 连续重过 1 → 2 → 3：每一步都必须成功（旧守卫在第 2 步即抛"该工序已过站"）
    rp1 = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, sn=res.sn))
    assert rp1.passed_op.seq == 1
    assert rp1.is_finished is False
    rp2 = pass_svc.pass_operation(OperationPassInput(work_station_id=w2.id, sn=res.sn))
    assert rp2.passed_op.seq == 2
    assert rp2.is_finished is False
    rp3 = pass_svc.pass_operation(OperationPassInput(work_station_id=w3.id, sn=res.sn))
    assert rp3.passed_op.seq == 3
    assert rp3.is_finished is True

    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_operation_seq == 3
    assert su.status == "finished"
