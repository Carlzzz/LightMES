import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.query_service import MasterDataQueryService


def _line_with_stations(svc):
    line = svc.create_line(LineCreate(code="OPL", name="线"))
    w1 = svc.create_work_station(WorkStationCreate(code="OPW1", name="站1", line_id=line.id, seq=1))
    w2 = svc.create_work_station(WorkStationCreate(code="OPW2", name="站2", line_id=line.id, seq=2))
    return line, w1, w2


def test_create_routing_with_operations_ordered(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="OPP", name="壳", type="finished"))
    line, w1, w2 = _line_with_stations(svc)
    r = svc.create_routing(RoutingCreate(code="OPR", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
        OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
    ]))
    ops = MasterDataQueryService(db_session).get_operations(r.id)
    assert [o.seq for o in ops] == [1, 2]
    assert ops[0].default_work_station_id == w1.id


def test_duplicate_seq_rejected(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="OPP2", name="壳", type="finished"))
    line, w1, w2 = _line_with_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="OPR2", name="x", product_id=p.id, operations=[
            OperationCreate(seq=1, code="A", name="a", default_work_station_id=w1.id),
            OperationCreate(seq=1, code="B", name="b", default_work_station_id=w2.id),
        ]))


def test_unknown_work_station_rejected(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="OPP3", name="壳", type="finished"))
    _line_with_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="OPR3", name="x", product_id=p.id, operations=[
            OperationCreate(seq=1, code="A", name="a", default_work_station_id=999999)]))
