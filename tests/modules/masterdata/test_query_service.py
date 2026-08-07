from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate,
    RoutingCreate, OperationCreate,
)


def _line(db_session):
    """建新三层路线（operation）。"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="QP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="QPL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(
        code="QPW1", name="作业站1", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="QPW2", name="作业站2", line_id=line.id, seq=2))
    r = md.create_routing(RoutingCreate(code="QR", name="路线", product_id=p.id,
        operations=[
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id, allowed_work_station_ids=[w2.id]),
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id, allowed_work_station_ids=[w1.id]),
        ]))
    return p, r


def test_get_operations_sorted_by_seq(db_session):
    p, r = _line(db_session)
    q = MasterDataQueryService(db_session)
    ops = q.get_operations(r.id)
    assert [o.seq for o in ops] == [1, 2]
    assert ops[0].name == "上料"


def test_get_product_and_routing(db_session):
    p, r = _line(db_session)
    q = MasterDataQueryService(db_session)
    assert q.get_product(p.id).code == "QP"
    assert q.get_routing(r.id).id == r.id
    assert q.get_product(999999) is None


def test_get_operations_empty_for_unknown_routing(db_session):
    q = MasterDataQueryService(db_session)
    assert q.get_operations(999999) == []
