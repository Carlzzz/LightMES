import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import WorkOrderCreate


def _setup(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WLP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="WLL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="WLW1", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="WLR", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id)]))
    return p, line, r


def test_create_work_order_binds_line(db_session):
    p, line, r = _setup(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="WL-WO1", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10))
    assert wo.line_id == line.id
    assert wo.status == "created"


def test_create_work_order_unknown_line_rejected(db_session):
    p, line, r = _setup(db_session)
    with pytest.raises(ValueError):
        ProductionService(db_session).create_work_order(WorkOrderCreate(
            code="WL-WO2", product_id=p.id, routing_id=r.id, line_id=999999, qty=1))
