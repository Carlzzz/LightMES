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


def test_rework_unknown_sn(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    with pytest.raises(NotFoundError):
        ReworkService(db_session).rework("NOPE", target_seq=0)
