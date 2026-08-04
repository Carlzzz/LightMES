import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)


def _setup_product_and_work_stations(svc):
    p = svc.create_product(ProductCreate(code="P1", name="壳", type="finished"))
    line = svc.create_line(LineCreate(code="P1L", name="线"))
    w1 = svc.create_work_station(WorkStationCreate(
        code="P1W1", name="作业站1", line_id=line.id, seq=1))
    w2 = svc.create_work_station(WorkStationCreate(
        code="P1W2", name="作业站2", line_id=line.id, seq=2))
    return p, w1, w2


def test_create_routing_with_operations(db_session):
    svc = MasterDataService(db_session)
    p, w1, w2 = _setup_product_and_work_stations(svc)
    r = svc.create_routing(RoutingCreate(
        code="R1", name="主路线", product_id=p.id,
        operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
        ],
    ))
    assert r.id is not None
    assert r.status == "active"
    ops = svc.routings.operations_of(r.id)
    assert [o.seq for o in ops] == [1, 2]


def test_second_routing_for_same_product_is_inactive(db_session):
    svc = MasterDataService(db_session)
    p, w1, w2 = _setup_product_and_work_stations(svc)
    svc.create_routing(RoutingCreate(code="R1", name="v1", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="a", default_work_station_id=w1.id)]))
    r2 = svc.create_routing(RoutingCreate(code="R2", name="v2", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="a", default_work_station_id=w1.id)]))
    assert r2.status == "inactive"


def test_duplicate_seq_rejected(db_session):
    svc = MasterDataService(db_session)
    p, w1, w2 = _setup_product_and_work_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="R9", name="x", product_id=p.id,
            operations=[
                OperationCreate(seq=1, code="OP1", name="a", default_work_station_id=w1.id),
                OperationCreate(seq=1, code="OP2", name="b", default_work_station_id=w2.id),
            ]))


def test_unknown_work_station_rejected(db_session):
    svc = MasterDataService(db_session)
    p, w1, w2 = _setup_product_and_work_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="R8", name="x", product_id=p.id,
            operations=[OperationCreate(seq=1, code="OP1", name="a", default_work_station_id=99999)]))


def test_db_rejects_two_active_routings_for_product(db_session):
    from sqlalchemy.exc import IntegrityError
    from lightmes.modules.masterdata.models import Routing
    svc = MasterDataService(db_session)
    p, w1, w2 = _setup_product_and_work_stations(svc)
    svc.create_routing(RoutingCreate(code="RA", name="v1", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="a", default_work_station_id=w1.id)]))
    # bypass the service rule: force a 2nd active routing directly
    db_session.add(Routing(code="RB", name="v2", product_id=p.id, version="2", status="active"))
    with pytest.raises(IntegrityError):
        db_session.flush()
