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
from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import (
    ComponentInput,
    OperationPassInput,
    SnRuleCreate,
    WorkOrderCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.shared.errors import BusinessRuleError


def _released_order_with_frozen_bom(db_session):
    md = MasterDataService(db_session)
    product = md.create_product(
        ProductCreate(code="SB-F", name="成品", type="finished"))
    original = md.create_product(
        ProductCreate(code="SB-OLD", name="旧件", type="component",
                      track_mode="serial"))
    replacement = md.create_product(
        ProductCreate(code="SB-NEW", name="新件", type="component",
                      track_mode="serial"))
    original_bom = md.create_bom(BomCreate(product_id=product.id, items=[
        BomItemCreate(component_product_id=original.id,
                      consume_at_operation_seq=1),
    ]))
    line = md.create_line(LineCreate(code="SB-L", name="线"))
    station = md.create_work_station(WorkStationCreate(
        code="SB-W", name="站", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(
        code="SB-R", name="路线", product_id=product.id,
        operations=[OperationCreate(
            seq=1, code="SB-OP", name="装配",
            default_work_station_id=station.id,
            allowed_work_station_ids=[station.id],
            require_material_binding=True),
        ]))
    production = ProductionService(db_session)
    rule = production.create_sn_rule(
        SnRuleCreate(code="SB-SN", name="SN", pattern="SB{SEQ:4}"))
    work_order = production.create_work_order(WorkOrderCreate(
        code="SB-WO", product_id=product.id, routing_id=routing.id,
        line_id=line.id, qty=2, sn_rule_id=rule.id))
    production.release_work_order(work_order.id)

    replacement_bom = md.create_bom(BomCreate(product_id=product.id, items=[
        BomItemCreate(component_product_id=replacement.id,
                      consume_at_operation_seq=1),
    ]))
    md.set_bom_status(replacement_bom.id, "active")
    db_session.refresh(original_bom)
    assert original_bom.status == "inactive"

    db_session.add(SerialUnit(sn="SB-COMP-OLD", product_id=original.id))
    db_session.add(SerialUnit(sn="SB-COMP-NEW", product_id=replacement.id))
    db_session.flush()
    return work_order, station, original, replacement


def test_binding_keeps_using_work_order_bom_snapshot(db_session):
    work_order, station, original, replacement = (
        _released_order_with_frozen_bom(db_session))

    result = OperationPassService(db_session).pass_operation(
        OperationPassInput(
            work_station_id=station.id,
            work_order_code=work_order.code,
            components=[ComponentInput(
                component_product_id=original.id,
                component_sn="SB-COMP-OLD",
            )],
        ))

    serial_unit = SerialUnitRepository(db_session).get_by_sn(result.sn)
    binds = GenealogyBindRepository(db_session).list_active_by_parent(
        serial_unit.id)
    assert [b.component_product_id for b in binds] == [original.id]


def test_active_bom_change_cannot_add_component_to_released_order(db_session):
    work_order, station, original, replacement = (
        _released_order_with_frozen_bom(db_session))

    with pytest.raises(BusinessRuleError):
        OperationPassService(db_session).pass_operation(
            OperationPassInput(
                work_station_id=station.id,
                work_order_code=work_order.code,
                components=[ComponentInput(
                    component_product_id=replacement.id,
                    component_sn="SB-COMP-NEW",
                )],
            ))
