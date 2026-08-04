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
from lightmes.modules.trace.trace_service import TraceService
from lightmes.shared.errors import NotFoundError, ValidationError


def _pass_with_components(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="TF", name="成品", type="finished"))
    c = md.create_product(
        ProductCreate(code="TC", name="主板", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c.id, qty=1)]))
    s = md.create_station(StationCreate(code="TS", name="装配"))
    r = md.create_routing(RoutingCreate(code="TR", name="路线", product_id=fin.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="TRL", name="r", pattern="T{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="TWO", product_id=fin.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    res = StationPassService(db_session).pass_station(StationPassInput(
        station_id=s.id, work_order_code="TWO",
        components=[ComponentInput(component_product_id=c.id, component_sn="MB-100")]))
    return res.sn


def test_genealogy_forward(db_session):
    sn = _pass_with_components(db_session)
    view = TraceService(db_session).genealogy_of(sn)
    assert view.sn == sn
    assert len(view.components) == 1
    assert view.components[0].component_ref == "MB-100"


def test_where_used_reverse(db_session):
    sn = _pass_with_components(db_session)
    parents = TraceService(db_session).where_used(component_sn="MB-100")
    assert len(parents) == 1
    assert parents[0].status == "active"


def test_history_includes_passes_and_components(db_session):
    sn = _pass_with_components(db_session)
    h = TraceService(db_session).history_of(sn)
    assert h.sn == sn
    assert len(h.passes) == 1
    assert len(h.components) == 1


def test_genealogy_unknown_sn(db_session):
    with pytest.raises(NotFoundError):
        TraceService(db_session).genealogy_of("NOPE")


def test_where_used_requires_a_key(db_session):
    with pytest.raises(ValidationError):
        TraceService(db_session).where_used()
