from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, LineCreate, WorkStationCreate,
    RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.models import RoutingStep


def _line(db_session):
    """建新三层路线（operation）+ 为旧 get_ordered_steps 层补建 routing_step。"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="QP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="QPL", name="线"))
    s1 = md.create_station(StationCreate(code="QS1", name="工位1"))
    s2 = md.create_station(StationCreate(code="QS2", name="工位2"))
    w1 = md.create_work_station(WorkStationCreate(
        code="QPW1", name="作业站1", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="QPW2", name="作业站2", line_id=line.id, seq=2))
    r = md.create_routing(RoutingCreate(code="QR", name="路线", product_id=p.id,
        operations=[
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
        ]))
    # 旧 facade get_ordered_steps 仍读 routing_steps —— 补建
    db_session.add_all([
        RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"),
        RoutingStep(routing_id=r.id, seq=2, station_id=s2.id, name="装配"),
    ])
    db_session.flush()
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
