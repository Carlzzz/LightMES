from sqlalchemy import select

import pytest

from lightmes.modules.masterdata.schemas import (
    BomCreate,
    BomItemCreate,
    LineCreate,
    OperationCreate,
    ProductCreate,
    RoutingCreate,
    WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.models import Batch, MaterialLot, SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.service import ProductionService
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import NotFoundError


def _setup(db_session):
    md = MasterDataService(db_session)
    finished = md.create_product(ProductCreate(code="MRF", name="成品", type="finished"))
    component = md.create_product(
        ProductCreate(code="MRB", name="批次料", type="component", track_mode="batch")
    )
    md.create_bom(BomCreate(
        product_id=finished.id,
        items=[BomItemCreate(component_product_id=component.id, qty=3)],
    ))

    line = md.create_line(LineCreate(code="MRL", name="线"))
    work_station = md.create_work_station(WorkStationCreate(
        code="MRW", name="站", line_id=line.id, seq=1,
    ))
    routing = md.create_routing(RoutingCreate(
        code="MRR", name="路线", product_id=finished.id,
        operations=[OperationCreate(
            seq=1, code="OP1", name="装配",
            default_work_station_id=work_station.id,
            allowed_work_station_ids=[work_station.id],
        )],
    ))

    production = ProductionService(db_session)
    rule = production.create_sn_rule(SnRuleCreate(code="MRS", name="r", pattern="MR{SEQ:4}"))
    work_order = production.create_work_order(WorkOrderCreate(
        code="MRWO", product_id=finished.id, routing_id=routing.id,
        line_id=line.id, qty=1, sn_rule_id=rule.id,
    ))
    production.release_work_order(work_order.id)

    batch = db_session.execute(
        select(Batch).where(Batch.work_order_id == work_order.id)
    ).scalar_one()
    serial_unit = SerialUnitRepository(db_session).add(
        SerialUnit(sn="MR-SN-1", work_order_id=work_order.id, product_id=finished.id)
    )
    return finished, component, batch, serial_unit


def test_unbind_returns_batch_material(db_session):
    finished, component, batch, serial_unit = _setup(db_session)
    lots = MaterialLotService(db_session)
    lot = lots.receive(code="MR-LOT-1", product_id=component.id, quantity=10)
    lots.release(lot.code)
    lots.consume(
        batch_id=batch.id,
        operation_record_id=None,
        product_id=component.id,
        lot_code=lot.code,
        quantity=3,
    )

    consumed = db_session.get(MaterialLot, lot.id)
    assert float(consumed.available_quantity) == 7

    service = GenealogyService(db_session)
    binds = service.bind_components(serial_unit, [
        ComponentBind(component_product_id=component.id, component_batch_no=lot.code, qty=3),
    ], operator_id=None)
    unbound = service.unbind(binds[0].id, reason="返工换料", operator_id=None)

    assert unbound.status == "unbound"
    restored = db_session.get(MaterialLot, lot.id)
    assert float(restored.available_quantity) == 10


def test_bind_batch_unknown_lot_rejected(db_session):
    finished, component, batch, serial_unit = _setup(db_session)
    service = GenealogyService(db_session)
    with pytest.raises(NotFoundError):
        service.bind_components(serial_unit, [
            ComponentBind(
                component_product_id=component.id,
                component_batch_no="NO-SUCH-LOT",
                qty=3,
            ),
        ], operator_id=None)
