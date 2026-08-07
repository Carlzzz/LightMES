from lightmes.shared.events import event_bus
from lightmes.modules.production.events import OperationPassed, SerialUnitFinished
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService


def _line(db_session, steps_n=1):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="EV", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="EVL", name="线"))
    wss = [md.create_work_station(WorkStationCreate(
        code=f"EVW{i}", name=f"作业站{i}", line_id=line.id, seq=i+1))
        for i in range(steps_n)]
    r = md.create_routing(RoutingCreate(code="EVR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                                    default_work_station_id=wss[i].id, allowed_work_station_ids=[wss[i].id])
                    for i in range(steps_n)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="EVRL", name="r", pattern="E{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="EVWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return wss, wo


def test_operation_passed_event_published(db_session):
    wss, wo = _line(db_session, steps_n=2)
    captured = []
    event_bus.subscribe(OperationPassed, lambda e: captured.append(e))
    svc = OperationPassService(db_session)
    res = svc.pass_operation(OperationPassInput(
        work_station_id=wss[0].id, work_order_code="EVWO"))
    assert any(e.sn == res.sn and e.work_station_id == wss[0].id for e in captured)


def test_serial_unit_finished_event_published(db_session):
    wss, wo = _line(db_session, steps_n=1)  # 单工序：首站即末站
    captured = []
    event_bus.subscribe(SerialUnitFinished, lambda e: captured.append(e))
    svc = OperationPassService(db_session)
    res = svc.pass_operation(OperationPassInput(
        work_station_id=wss[0].id, work_order_code="EVWO"))
    assert any(e.sn == res.sn for e in captured)
