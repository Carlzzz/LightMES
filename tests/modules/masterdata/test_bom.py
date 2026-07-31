import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, BomCreate, BomItemCreate,
)


def _finished_and_components(svc):
    fin = svc.create_product(ProductCreate(code="F1", name="成品", type="finished"))
    c_ser = svc.create_product(
        ProductCreate(code="C-SER", name="主板", type="component", track_mode="serial")
    )
    c_bat = svc.create_product(
        ProductCreate(code="C-BAT", name="螺丝", type="consumable", track_mode="batch")
    )
    return fin, c_ser, c_bat


def test_create_bom_copies_component_track_mode(db_session):
    svc = MasterDataService(db_session)
    fin, c_ser, c_bat = _finished_and_components(svc)
    bom = svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id, qty=1),
        BomItemCreate(component_product_id=c_bat.id, qty=4),
    ]))
    items = {i.component_product_id: i for i in svc.boms.items_of(bom.id)}
    assert items[c_ser.id].track_mode == "serial"
    assert items[c_bat.id].track_mode == "batch"
    assert bom.status == "active"


def test_second_bom_for_same_product_inactive(db_session):
    svc = MasterDataService(db_session)
    fin, c_ser, _ = _finished_and_components(svc)
    svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id)]))
    bom2 = svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id)]))
    assert bom2.status == "inactive"


def test_unknown_component_rejected(db_session):
    svc = MasterDataService(db_session)
    fin, _, _ = _finished_and_components(svc)
    with pytest.raises(ValueError):
        svc.create_bom(BomCreate(product_id=fin.id, items=[
            BomItemCreate(component_product_id=99999)]))


def test_db_rejects_two_active_boms_for_product(db_session):
    from sqlalchemy.exc import IntegrityError
    from lightmes.modules.masterdata.models import Bom
    svc = MasterDataService(db_session)
    fin, c_ser, _ = _finished_and_components(svc)
    svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id)]))
    # bypass the service rule: force a 2nd active bom directly
    db_session.add(Bom(product_id=fin.id, version="2", status="active"))
    with pytest.raises(IntegrityError):
        db_session.flush()
