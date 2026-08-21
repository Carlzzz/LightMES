from lightmes.modules.masterdata.schemas import (
    LineCreate,
    OperationCreate,
    ProductCreate,
    RoutingCreate,
    WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.models import Batch
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import (
    OperationPassInput,
    SnRuleCreate,
    WorkOrderCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.trace.rework_service import ReworkService


def test_scrap_finished_unit_moves_counter_from_produced_to_scrap(db_session):
    md = MasterDataService(db_session)
    product = md.create_product(
        ProductCreate(code="SA-F", name="成品", type="finished"))
    line = md.create_line(LineCreate(code="SA-L", name="线"))
    station_1 = md.create_work_station(WorkStationCreate(
        code="SA-W1", name="一站", line_id=line.id, seq=1))
    station_2 = md.create_work_station(WorkStationCreate(
        code="SA-W2", name="二站", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(
        code="SA-R", name="路线", product_id=product.id,
        operations=[
            OperationCreate(seq=1, code="SA-OP1", name="一",
                            default_work_station_id=station_1.id,
                            allowed_work_station_ids=[station_1.id]),
            OperationCreate(seq=2, code="SA-OP2", name="二",
                            default_work_station_id=station_2.id,
                            allowed_work_station_ids=[station_2.id]),
        ]))
    production = ProductionService(db_session)
    rule = production.create_sn_rule(
        SnRuleCreate(code="SA-SN", name="SN", pattern="SA{SEQ:4}"))
    work_order = production.create_work_order(WorkOrderCreate(
        code="SA-WO", product_id=product.id, routing_id=routing.id,
        line_id=line.id, qty=2, sn_rule_id=rule.id))
    production.release_work_order(work_order.id)

    pass_service = OperationPassService(db_session)
    first = pass_service.pass_operation(OperationPassInput(
        work_station_id=station_1.id, work_order_code=work_order.code))
    finished = pass_service.pass_operation(OperationPassInput(
        work_station_id=station_2.id, sn=first.sn))
    assert finished.is_finished is True

    serial_unit = SerialUnitRepository(db_session).get_by_sn(first.sn)
    batch = db_session.get(Batch, serial_unit.batch_id)
    assert work_order.produced_qty == 1
    assert work_order.scrap_qty == 0
    assert batch.produced_qty == 1

    ReworkService(db_session).scrap(first.sn, reason="完工后报废")
    db_session.refresh(work_order)
    db_session.refresh(batch)
    db_session.refresh(serial_unit)

    assert serial_unit.status == "scrapped"
    assert work_order.produced_qty == 0
    assert work_order.scrap_qty == 1
    assert batch.produced_qty == 0
