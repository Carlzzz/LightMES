from lightmes.modules.production.models import SerialUnit, StationPass
from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository,
)


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


def test_station_pass_exists_check(db_session):
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, StationCreate, LineCreate, WorkStationCreate,
        RoutingCreate, OperationCreate,
    )
    from lightmes.modules.masterdata.models import RoutingStep
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="SPP", name="壳", type="finished"))
    s = md.create_station(StationCreate(code="SPS", name="工位"))
    line = md.create_line(LineCreate(code="SPL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="SPW", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="SPR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id)]))
    # 旧 production 层仍写 station_pass（FK 到 routing_steps/stations）——补建旧层数据
    db_session.add(RoutingStep(routing_id=r.id, seq=1, station_id=s.id, name="装配"))
    db_session.flush()
    step_id = md.routings.steps_of(r.id)[0].id
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="SPWO", product_id=p.id, routing_id=r.id,
                        line_id=line.id, qty=5))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="SN9", work_order_id=wo.id, product_id=p.id))
    sp_repo = StationPassRepository(db_session)
    assert sp_repo.exists_pass(su.id, step_id) is False
    sp_repo.add(StationPass(serial_unit_id=su.id, work_order_id=wo.id,
        routing_step_id=step_id, station_id=s.id, result="pass"))
    assert sp_repo.exists_pass(su.id, step_id) is True
