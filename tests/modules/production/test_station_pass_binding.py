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
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.shared.errors import BusinessRuleError


def _line(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BF", name="成品", type="finished"))
    comp = md.create_product(
        ProductCreate(code="BC", name="螺丝", type="consumable", track_mode="batch"))
    other = md.create_product(
        ProductCreate(code="BX", name="非BOM件", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=comp.id, qty=4)]))
    s1 = md.create_station(StationCreate(code="BS1", name="上料"))
    s2 = md.create_station(StationCreate(code="BS2", name="装配"))
    r = md.create_routing(RoutingCreate(code="BR", name="路线", product_id=fin.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="BRL", name="r", pattern="B{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="BWO", product_id=fin.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, comp, other, s1, s2, wo


def test_pass_with_component_binds(db_session):
    fin, comp, other, s1, s2, wo = _line(db_session)
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(
        station_id=s1.id, work_order_code="BWO",
        components=[ComponentInput(component_product_id=comp.id,
                                   component_batch_no="LOT-1", qty=4)]))
    assert res.bound_count == 1
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    binds = GenealogyBindRepository(db_session).list_active_by_parent(su.id)
    assert len(binds) == 1
    assert binds[0].component_batch_no == "LOT-1"


def test_bad_component_rolls_back_whole_pass(db_session):
    fin, comp, other, s1, s2, wo = _line(db_session)
    svc = StationPassService(db_session)
    # other 不在 BOM → 绑定失败 → 整个过站回滚，不应留下 SerialUnit
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(
            station_id=s1.id, work_order_code="BWO",
            components=[ComponentInput(component_product_id=other.id,
                                       component_sn="X-1")]))
    # 关键断言：过站被拒后无残留 SerialUnit（同事务回滚）
    assert SerialUnitRepository(db_session).list_by_work_order(wo.id) == []


def test_pass_without_components_bound_count_zero(db_session):
    fin, comp, other, s1, s2, wo = _line(db_session)
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="BWO"))
    assert res.bound_count == 0
