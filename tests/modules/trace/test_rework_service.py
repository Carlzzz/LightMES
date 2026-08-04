import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput, ComponentInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
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
    s1 = md.create_station(StationCreate(code="RS1", name="上料"))
    s2 = md.create_station(StationCreate(code="RS2", name="装配"))
    r = md.create_routing(RoutingCreate(code="RR", name="路线", product_id=fin.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRL", name="r", pattern="R{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWO", product_id=fin.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, comp, s1, s2, wo


def test_rework_rolls_back_step_and_status(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_step_seq == 1
    reworked = ReworkService(db_session).rework(res.sn, target_seq=0, reason="上料错误")
    assert reworked.status == "reworking"
    assert reworked.current_step_seq == 0


def test_rework_unbinds_components(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(
        station_id=s1.id, work_order_code="RWO",
        components=[ComponentInput(component_product_id=comp.id,
                                   component_batch_no="LOT-1", qty=4)]))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    bind = GenealogyBindRepository(db_session).list_active_by_parent(su.id)[0]
    ReworkService(db_session).rework(res.sn, target_seq=0,
                                     unbind_bind_ids=[bind.id], reason="换料")
    assert GenealogyBindRepository(db_session).list_active_by_parent(su.id) == []


def test_rework_then_repass_resets_in_process(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    ReworkService(db_session).rework(res.sn, target_seq=0)
    # 重新过首站：reworking → in_process
    r2 = pass_svc.pass_station(StationPassInput(station_id=s1.id, sn=res.sn))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.status == "in_process"
    assert su.current_step_seq == 1


def test_rework_target_seq_must_be_less(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    with pytest.raises(ValidationError):
        ReworkService(db_session).rework(res.sn, target_seq=5)  # >= current


def test_scrap_terminal(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    scrapped = ReworkService(db_session).scrap(res.sn, reason="报废")
    assert scrapped.status == "scrapped"
    # scrapped 后不可过站
    with pytest.raises(BusinessRuleError):
        pass_svc.pass_station(StationPassInput(station_id=s2.id, sn=res.sn))


def _three_step_line(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="RF3", name="成品", type="finished"))
    s1 = md.create_station(StationCreate(code="RS31", name="上料"))
    s2 = md.create_station(StationCreate(code="RS32", name="装配"))
    s3 = md.create_station(StationCreate(code="RS33", name="测试"))
    r = md.create_routing(RoutingCreate(code="RR3", name="路线", product_id=fin.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
            RoutingStepCreate(seq=3, station_id=s3.id, name="测试"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRL3", name="r", pattern="R3{SEQ:2}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWO3", product_id=fin.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, s1, s2, s3, wo


def test_rework_unknown_sn(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    with pytest.raises(NotFoundError):
        ReworkService(db_session).rework("NOPE", target_seq=0)


def test_rework_then_multistep_repass_all_steps(db_session):
    """回归：3 步路线全部过完后返工回退，再连续重新过 1→2→3 每一步都应成功。

    旧守卫 `status != "reworking"` 只豁免返工后的首次重过：第 2 次重过时 SN 状态
    已被复位为 in_process，命中原始运行的 pass 记录，误报"该工序已过站"。§5.4 要求
    旧 pass 记录"保留但不阻挡"，故需无条件放行（期望下一工序的 seq>current_step_seq
    选择逻辑本身就是防重复机制）。
    """
    fin, s1, s2, s3, wo = _three_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO3"))
    r2 = pass_svc.pass_station(StationPassInput(station_id=s2.id, sn=res.sn))
    r3 = pass_svc.pass_station(StationPassInput(station_id=s3.id, sn=res.sn))
    assert r3.passed_step.seq == 3
    assert r3.is_finished is True
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_step_seq == 3
    assert su.status == "finished"

    # 完工件可返工（rework 仅拒 scrapped，且 target_seq < current_step_seq）
    reworked = ReworkService(db_session).rework(res.sn, target_seq=0, reason="返修")
    assert reworked.status == "reworking"
    assert reworked.current_step_seq == 0

    # 连续重过 1 → 2 → 3：每一步都必须成功（旧守卫在第 2 步即抛"该工序已过站"）
    rp1 = pass_svc.pass_station(StationPassInput(station_id=s1.id, sn=res.sn))
    assert rp1.passed_step.seq == 1
    assert rp1.is_finished is False
    rp2 = pass_svc.pass_station(StationPassInput(station_id=s2.id, sn=res.sn))
    assert rp2.passed_step.seq == 2
    assert rp2.is_finished is False
    rp3 = pass_svc.pass_station(StationPassInput(station_id=s3.id, sn=res.sn))
    assert rp3.passed_step.seq == 3
    assert rp3.is_finished is True

    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_step_seq == 3
    assert su.status == "finished"
