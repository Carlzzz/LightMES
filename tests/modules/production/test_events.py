from lightmes.shared.events import event_bus
from lightmes.modules.production.events import StationPassed, SerialUnitFinished
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, StationPassInput
from lightmes.modules.production.station_pass_service import StationPassService


def _line(db_session, steps_n=1):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="EV", name="壳", type="finished"))
    stations = [md.create_station(StationCreate(code=f"EVS{i}", name=f"工位{i}"))
                for i in range(steps_n)]
    r = md.create_routing(RoutingCreate(code="EVR", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=i+1, station_id=stations[i].id, name=f"工序{i+1}")
               for i in range(steps_n)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="EVRL", name="r", pattern="E{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="EVWO", product_id=p.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return stations, wo


def test_station_passed_event_published(db_session):
    stations, wo = _line(db_session, steps_n=2)
    captured = []
    event_bus.subscribe(StationPassed, lambda e: captured.append(e))
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=stations[0].id, work_order_code="EVWO"))
    assert any(e.sn == res.sn and e.station_id == stations[0].id for e in captured)


def test_serial_unit_finished_event_published(db_session):
    stations, wo = _line(db_session, steps_n=1)  # 单工序：首站即末站
    captured = []
    event_bus.subscribe(SerialUnitFinished, lambda e: captured.append(e))
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=stations[0].id, work_order_code="EVWO"))
    assert any(e.sn == res.sn for e in captured)
