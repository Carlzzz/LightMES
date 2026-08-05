from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository


def test_serial_unit_persist_and_lookup(db_session):
    # 需要一个 work_order + product；直接建最小依赖
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="SUP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="SUL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="SUW", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="SUR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id)]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="SUWO", product_id=p.id, routing_id=r.id,
                        line_id=line.id, qty=5))
    repo = SerialUnitRepository(db_session)
    su = repo.add(SerialUnit(sn="SN0001", work_order_id=wo.id, product_id=p.id))
    assert su.id is not None
    assert su.status == "in_process"
    assert su.version == 0
    assert repo.get_by_sn("SN0001").id == su.id
