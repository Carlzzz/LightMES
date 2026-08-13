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


def _release_order(db_session, code, sn_rule=None):
    md = MasterDataService(db_session)
    product = md.create_product(ProductCreate(code=f"{code}-P", name="P", type="finished"))
    line = md.create_line(LineCreate(code=f"{code}-L", name="L"))
    ws = md.create_work_station(WorkStationCreate(code=f"{code}-W", name="W", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(
        code=f"{code}-R", name="R", product_id=product.id,
        operations=[OperationCreate(seq=1, code="OP1", name="OP1",
                                    default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    if sn_rule is None:
        sn_rule = prod.create_sn_rule(SnRuleCreate(code=f"{code}-S", name="r", pattern="BF{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code=f"{code}-WO", product_id=product.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=sn_rule.id))
    return prod.release_work_order(wo.id)


def test_backfill_skips_existing_snapshots(db_session):
    wo = _release_order(db_session, "BF-SKIP")
    marker = {"routing": {"id": 999}}
    wo.process_snapshot = marker
    db_session.flush()

    updated = backfill_work_order_snapshots(db_session)

    db_session.refresh(wo)
    assert updated == 0
    assert wo.process_snapshot == marker


def test_backfill_ignores_non_active_statuses(db_session):
    wo = _release_order(db_session, "BF-NONACTIVE")
    wo.status = "created"
    wo.process_snapshot = None
    db_session.flush()

    updated = backfill_work_order_snapshots(db_session)

    db_session.refresh(wo)
    assert updated == 0
    assert wo.process_snapshot is None


def test_backfill_updates_multiple_matching_orders(db_session):
    prod = ProductionService(db_session)
    sn_rule = prod.create_sn_rule(SnRuleCreate(code="BF-MULTI-S", name="r", pattern="BF{SEQ:4}"))
    wo1 = _release_order(db_session, "BF-MULTI-1", sn_rule=sn_rule)
    wo2 = _release_order(db_session, "BF-MULTI-2", sn_rule=sn_rule)
    wo1.process_snapshot = None
    wo2.process_snapshot = None
    db_session.flush()

    updated = backfill_work_order_snapshots(db_session)

    db_session.refresh(wo1)
    db_session.refresh(wo2)
    assert updated == 2
    assert wo1.process_snapshot is not None
    assert wo2.process_snapshot is not None
