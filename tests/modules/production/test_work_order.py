import pytest
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="WPL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="WPW", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="WR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id)]))
    return p, r, line


def test_create_sn_rule_validates_pattern(db_session):
    svc = ProductionService(db_session)
    with pytest.raises(ValueError):
        svc.create_sn_rule(SnRuleCreate(code="BAD", name="x", pattern="{SEQ}"))


def test_create_and_release_work_order(db_session):
    p, r, line = _line(db_session)
    svc = ProductionService(db_session)
    wo = svc.create_work_order(WorkOrderCreate(
        code="WO-1", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10))
    assert wo.status == "created"
    released = svc.release_work_order(wo.id)
    assert released.status == "released"


def test_release_non_created_rejected(db_session):
    p, r, line = _line(db_session)
    svc = ProductionService(db_session)
    wo = svc.create_work_order(WorkOrderCreate(
        code="WO-2", product_id=p.id, routing_id=r.id, line_id=line.id, qty=5))
    svc.release_work_order(wo.id)
    with pytest.raises(ValueError):
        svc.release_work_order(wo.id)  # already released


def test_create_work_order_unknown_product_rejected(db_session):
    p, r, line = _line(db_session)
    svc = ProductionService(db_session)
    with pytest.raises(ValueError):
        svc.create_work_order(WorkOrderCreate(
            code="WO-3", product_id=99999, routing_id=r.id, line_id=line.id, qty=1))
