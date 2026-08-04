from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import WorkOrderCreate
from lightmes.modules.production.models import (
    SerialUnit, OperationRecord, OperationParam,
)
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository, OperationParamRepository,
)


def _fixture(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="ORP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="ORL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="ORW1", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="ORR", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id)]))
    op = md.routings.operations_of(r.id)[0]
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="OR-WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=5))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="ORSN1", work_order_id=wo.id, product_id=p.id))
    return line, w1, op, wo, su


def test_operation_record_and_params(db_session):
    line, w1, op, wo, su = _fixture(db_session)
    rec = OperationRecordRepository(db_session).add(OperationRecord(
        serial_unit_id=su.id, work_order_id=wo.id, operation_id=op.id,
        work_station_id=w1.id, line_id=line.id, result="pass"))
    assert rec.id is not None
    prepo = OperationParamRepository(db_session)
    prepo.add(OperationParam(operation_record_id=rec.id,
              param_key="扭矩", param_value="1.2", unit="N·m", source="manual"))
    recs = OperationRecordRepository(db_session).list_by_serial_unit(su.id)
    assert [r.id for r in recs] == [rec.id]
    params = prepo.list_by_serial_unit(su.id)  # 工艺参数追溯：跨记录汇集
    assert len(params) == 1 and params[0].param_key == "扭矩"
