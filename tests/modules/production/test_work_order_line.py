import pytest
from sqlalchemy.exc import IntegrityError
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


def test_work_order_routing_product_mismatch_rejected(db_session):
    md = MasterDataService(db_session)
    p_a = md.create_product(ProductCreate(code="WLP-A", name="壳A", type="finished"))
    p_b = md.create_product(ProductCreate(code="WLP-B", name="壳B", type="finished"))
    line = md.create_line(LineCreate(code="WLL-AB", name="线AB"))
    w1 = md.create_work_station(WorkStationCreate(
        code="WLW-AB1", name="站", line_id=line.id, seq=1))
    r_b = md.create_routing(RoutingCreate(code="WLR-B", name="路线B", product_id=p_b.id, operations=[
        OperationCreate(seq=1, code="OPB1", name="上料", default_work_station_id=w1.id)]))
    with pytest.raises(ValueError):
        ProductionService(db_session).create_work_order(WorkOrderCreate(
            code="WL-WO-M", product_id=p_a.id, routing_id=r_b.id, line_id=line.id, qty=10))


def test_work_order_operation_station_off_line_rejected(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WLP-C", name="壳C", type="finished"))
    l1 = md.create_line(LineCreate(code="WLL1", name="线1"))
    l2 = md.create_line(LineCreate(code="WLL2", name="线2"))
    w2 = md.create_work_station(WorkStationCreate(
        code="WLW2", name="站2", line_id=l2.id, seq=1))
    r = md.create_routing(RoutingCreate(code="WLR-C", name="路线C", product_id=p.id, operations=[
        OperationCreate(seq=1, code="OPC1", name="上料", default_work_station_id=w2.id)]))
    with pytest.raises(ValueError):
        ProductionService(db_session).create_work_order(WorkOrderCreate(
            code="WL-WO-OFF", product_id=p.id, routing_id=r.id, line_id=l1.id, qty=10))


def test_routing_duplicate_operation_code_rejected(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WLP-D", name="壳D", type="finished"))
    line = md.create_line(LineCreate(code="WLL-D", name="线D"))
    w1 = md.create_work_station(WorkStationCreate(
        code="WLW-D1", name="站", line_id=line.id, seq=1))
    with pytest.raises(IntegrityError):
        md.create_routing(RoutingCreate(code="WLR-D", name="路线D", product_id=p.id, operations=[
            OperationCreate(seq=1, code="OPD", name="上料", default_work_station_id=w1.id),
            OperationCreate(seq=2, code="OPD", name="下料", default_work_station_id=w1.id)]))
