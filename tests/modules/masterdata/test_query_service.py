from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="QP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="QS1", name="工位1"))
    s2 = md.create_station(StationCreate(code="QS2", name="工位2"))
    r = md.create_routing(RoutingCreate(code="QR", name="路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
        ]))
    return p, r


def test_get_ordered_steps_sorted_by_seq(db_session):
    p, r = _line(db_session)
    q = MasterDataQueryService(db_session)
    steps = q.get_ordered_steps(r.id)
    assert [s.seq for s in steps] == [1, 2]
    assert steps[0].name == "上料"


def test_get_product_and_routing(db_session):
    p, r = _line(db_session)
    q = MasterDataQueryService(db_session)
    assert q.get_product(p.id).code == "QP"
    assert q.get_routing(r.id).id == r.id
    assert q.get_product(999999) is None


def test_get_ordered_steps_empty_for_unknown_routing(db_session):
    q = MasterDataQueryService(db_session)
    assert q.get_ordered_steps(999999) == []
