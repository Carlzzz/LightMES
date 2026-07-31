import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)


def _setup_product_and_stations(svc):
    p = svc.create_product(ProductCreate(code="P1", name="壳", type="finished"))
    s1 = svc.create_station(StationCreate(code="S1", name="工位1"))
    s2 = svc.create_station(StationCreate(code="S2", name="工位2"))
    return p, s1, s2


def test_create_routing_with_steps(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    r = svc.create_routing(RoutingCreate(
        code="R1", name="主路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ],
    ))
    assert r.id is not None
    assert r.status == "active"
    steps = svc.routings.steps_of(r.id)
    assert [s.seq for s in steps] == [1, 2]


def test_second_routing_for_same_product_is_inactive(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    svc.create_routing(RoutingCreate(code="R1", name="v1", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="a")]))
    r2 = svc.create_routing(RoutingCreate(code="R2", name="v2", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="a")]))
    assert r2.status == "inactive"


def test_duplicate_seq_rejected(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="R9", name="x", product_id=p.id,
            steps=[
                RoutingStepCreate(seq=1, station_id=s1.id, name="a"),
                RoutingStepCreate(seq=1, station_id=s2.id, name="b"),
            ]))


def test_unknown_station_rejected(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="R8", name="x", product_id=p.id,
            steps=[RoutingStepCreate(seq=1, station_id=99999, name="a")]))


def test_db_rejects_two_active_routings_for_product(db_session):
    from sqlalchemy.exc import IntegrityError
    from lightmes.modules.masterdata.models import Routing
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    svc.create_routing(RoutingCreate(code="RA", name="v1", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="a")]))
    # bypass the service rule: force a 2nd active routing directly
    db_session.add(Routing(code="RB", name="v2", product_id=p.id, version="2", status="active"))
    with pytest.raises(IntegrityError):
        db_session.flush()
