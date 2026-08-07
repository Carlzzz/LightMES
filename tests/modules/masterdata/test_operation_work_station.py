import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.repository import (
    OperationWorkStationRepository, WorkStationRepository,
)


def _setup(db_session, allowed_ids):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    wss = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(len(allowed_ids))]
    return md, p, wss


def test_create_routing_writes_allowed(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0, 1])
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序",
                        default_work_station_id=wss[0].id,
                        allowed_work_station_ids=[wss[0].id, wss[1].id])]))
    db_session.flush()
    ops = md.routings.operations_of(routing.id)
    allowed = OperationWorkStationRepository(db_session).list_by_operation(ops[0].id)
    assert {a.work_station_id for a in allowed} == {wss[0].id, wss[1].id}


def test_default_must_be_in_allowed(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0, 1])
    with pytest.raises(ValueError, match="默认作业站必须在允许"):
        md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序",
                            default_work_station_id=wss[0].id,
                            allowed_work_station_ids=[wss[1].id])]))  # default wss[0] 不在 allowed


def test_allowed_cannot_be_empty(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0])
    with pytest.raises(ValueError, match="至少指定一个允许作业站"):
        md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序",
                            default_work_station_id=wss[0].id,
                            allowed_work_station_ids=[])]))


def test_allowed_ws_must_exist(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0])
    with pytest.raises(ValueError, match="作业站不存在"):
        md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序",
                            default_work_station_id=wss[0].id,
                            allowed_work_station_ids=[wss[0].id, 999999])]))
