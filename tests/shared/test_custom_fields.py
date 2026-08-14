from sqlalchemy import select

from lightmes.modules.production.models import MaterialLot, WorkOrder
from lightmes.shared.custom_fields import (
    CustomFieldDefinition,
    CustomFieldService,
)


def _add_definition(db, entity_type, key, field_type):
    definition = CustomFieldDefinition(
        entity_type=entity_type,
        key=key,
        label=key,
        type=field_type,
    )
    db.add(definition)
    db.flush()
    return definition


def test_cast_values_uses_declared_types_and_basic_fallback(db_session):
    _add_definition(db_session, "work_order", "customer", "text")
    _add_definition(db_session, "work_order", "target_qty", "number")
    _add_definition(db_session, "work_order", "is_urgent", "boolean")
    _add_definition(db_session, "material_lot", "density", "number")
    _add_definition(db_session, "material_lot", "is_quarantine", "boolean")

    service = CustomFieldService(db_session)

    work_order_values = service.cast_values(
        "work_order",
        {
            "customer": "ACME",
            "target_qty": "42",
            "is_urgent": "true",
            "unmapped": "123",
        },
    )
    assert work_order_values == {
        "customer": "ACME",
        "target_qty": 42.0,
        "is_urgent": True,
        "unmapped": 123,
    }

    material_lot_values = service.cast_values(
        "material_lot",
        {
            "density": "1.25",
            "is_quarantine": "yes",
        },
    )
    assert material_lot_values == {
        "density": 1.25,
        "is_quarantine": True,
    }


def test_work_order_and_material_lot_store_custom_fields(
    db_session, full_station_setup
):
    setup = full_station_setup

    work_order = setup.work_order
    work_order.custom_fields = {
        "customer": "ACME",
        "target_qty": 42.0,
        "is_urgent": True,
    }

    material_lot = MaterialLot(
        code="CF-LOT-1",
        product_id=setup.product.id,
        quantity=5,
        available_quantity=5,
        custom_fields={"density": 1.25, "is_quarantine": True},
    )
    db_session.add(material_lot)
    db_session.flush()
    db_session.refresh(work_order)
    db_session.refresh(material_lot)

    assert work_order.custom_fields == {
        "customer": "ACME",
        "target_qty": 42.0,
        "is_urgent": True,
    }
    assert material_lot.custom_fields == {
        "density": 1.25,
        "is_quarantine": True,
    }

    reloaded_work_order = db_session.execute(
        select(WorkOrder).where(WorkOrder.id == work_order.id)
    ).scalar_one()
    reloaded_material_lot = db_session.execute(
        select(MaterialLot).where(MaterialLot.id == material_lot.id)
    ).scalar_one()

    assert reloaded_work_order.custom_fields["customer"] == "ACME"
    assert reloaded_material_lot.custom_fields["density"] == 1.25
