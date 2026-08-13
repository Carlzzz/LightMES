from sqlalchemy import select

from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.models import MaterialLot, StockMovement
from lightmes.shared.errors import BusinessRuleError
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate,
    LineCreate,
    WorkStationCreate,
    RoutingCreate,
    OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import Batch


def _batch(db_session, *, suffix):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code=f"MLP{suffix}", name="料", type="component"))
    line = md.create_line(LineCreate(code=f"MLL{suffix}", name="线"))
    ws = md.create_work_station(WorkStationCreate(
        code=f"MLW{suffix}", name="站", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(
        code=f"MLR{suffix}", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="工序1",
                                    default_work_station_id=ws.id,
                                    allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code=f"MLS{suffix}", name="r", pattern=f"ML{suffix}{{SEQ:4}}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code=f"MLW{suffix}", product_id=p.id, routing_id=routing.id,
        line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    batch = db_session.execute(select(Batch).where(Batch.work_order_id == wo.id)).scalar_one()
    return p, batch


def _movements(db_session, lot_id):
    return list(db_session.execute(
        select(StockMovement)
        .where(StockMovement.material_lot_id == lot_id)
        .order_by(StockMovement.id)
    ).scalars().all())


def test_receive_consume_return_writes_ordered_movements(db_session):
    product, batch = _batch(db_session, suffix="C")
    service = MaterialLotService(db_session)
    lot = service.receive(code="LOT-3", product_id=product.id, quantity=10)
    service.release(lot.code)

    service.consume(
        batch_id=batch.id,
        operation_record_id=None,
        product_id=product.id,
        lot_code=lot.code,
        quantity=10,
    )
    service.return_consumed(material_lot_id=lot.id, quantity=2, reason="返工退回")

    movements = _movements(db_session, lot.id)
    assert [m.movement_type for m in movements] == ["receive", "consume", "return"]
    assert [float(m.quantity) for m in movements] == [10, -10, 2]

    refreshed = db_session.get(MaterialLot, lot.id)
    assert float(refreshed.available_quantity) == 2
    assert float(refreshed.quantity) == 12
    assert refreshed.status == "released"


def test_insufficient_consume_writes_no_consume_movement(db_session):
    product, batch = _batch(db_session, suffix="D")
    service = MaterialLotService(db_session)
    lot = service.receive(code="LOT-4", product_id=product.id, quantity=1)
    service.release(lot.code)

    try:
        service.consume(
            batch_id=batch.id,
            operation_record_id=None,
            product_id=product.id,
            lot_code=lot.code,
            quantity=2,
        )
    except BusinessRuleError as exc:
        assert "可用数量不足" in str(exc)
    else:
        raise AssertionError("expected insufficient stock error")

    movements = _movements(db_session, lot.id)
    assert [m.movement_type for m in movements] == ["receive"]
