from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository


def _finished_sn(db_session):
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    from lightmes.modules.production.models import SerialUnit
    from lightmes.modules.production.repository import SerialUnitRepository
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="GBP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="GBL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="GBW", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="GBR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="GBWO", product_id=p.id, routing_id=r.id,
                        line_id=line.id, qty=5))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="GBSN1", work_order_id=wo.id, product_id=p.id))
    return su, p


def test_genealogy_bind_persist_and_query(db_session):
    su, p = _finished_sn(db_session)
    repo = GenealogyBindRepository(db_session)
    b = repo.add(GenealogyBind(
        parent_sn_id=su.id, component_product_id=p.id,
        component_type="serial", component_sn="COMP-1",
    ))
    assert b.id is not None
    assert b.status == "active"
    assert [x.id for x in repo.list_active_by_parent(su.id)] == [b.id]
    assert [x.id for x in repo.list_active_by_component_sn("COMP-1")] == [b.id]


def test_unbound_excluded_from_active_queries(db_session):
    su, p = _finished_sn(db_session)
    repo = GenealogyBindRepository(db_session)
    b = repo.add(GenealogyBind(
        parent_sn_id=su.id, component_product_id=p.id,
        component_type="batch", component_batch_no="LOT-9", status="unbound",
    ))
    assert repo.list_active_by_parent(su.id) == []
    assert [x.id for x in repo.list_by_parent(su.id)] == [b.id]  # 历史仍在
