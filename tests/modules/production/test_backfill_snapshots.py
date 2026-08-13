from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.service import ProductionService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.backfill_snapshots import backfill_work_order_snapshots


def test_backfill_sets_snapshot_for_active_work_orders(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="BF-P", name="P", type="finished"))
    line = md.create_line(LineCreate(code="BF-L", name="L"))
    ws = md.create_work_station(WorkStationCreate(code="BF-W", name="W", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(
        code="BF-R", name="R", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="OP1",
                                    default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="BF-S", name="r", pattern="BF{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="BF-WO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    wo.process_snapshot = None
    db_session.flush()

    updated = backfill_work_order_snapshots(db_session)

    db_session.refresh(wo)
    assert updated == 1
    assert wo.process_snapshot is not None
