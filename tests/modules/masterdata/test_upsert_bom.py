import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate, BomUpsert, BomItemUpsert


def _products(db_session):
    svc = MasterDataService(db_session)
    svc.create_product(ProductCreate(code="FIN", name="成品", type="finished"))
    svc.create_product(ProductCreate(code="C1", name="主板", type="component", track_mode="serial"))
    svc.create_product(ProductCreate(code="C2", name="螺丝", type="consumable", track_mode="batch"))
    return svc


def test_upsert_bom_creates(db_session):
    svc = _products(db_session)
    bom, action = svc.upsert_bom(BomUpsert(erp_ref="EB-1", product_code="FIN", items=[
        BomItemUpsert(component_code="C1", qty=1),
        BomItemUpsert(component_code="C2", qty=4)]))
    assert action == "created"
    assert bom.source == "erp"
    items = svc.boms.items_of(bom.id)
    assert {i.track_mode for i in items} == {"serial", "batch"}


def test_upsert_bom_replaces_items_on_update(db_session):
    svc = _products(db_session)
    svc.upsert_bom(BomUpsert(erp_ref="EB-2", product_code="FIN", items=[
        BomItemUpsert(component_code="C1", qty=1)]))
    bom, action = svc.upsert_bom(BomUpsert(erp_ref="EB-2", product_code="FIN", items=[
        BomItemUpsert(component_code="C2", qty=8)]))
    assert action == "updated"
    items = svc.boms.items_of(bom.id)
    assert len(items) == 1 and items[0].qty == 8


def test_upsert_bom_unknown_product_raises(db_session):
    svc = _products(db_session)
    with pytest.raises(ValueError):
        svc.upsert_bom(BomUpsert(erp_ref="EB-3", product_code="NOPE", items=[
            BomItemUpsert(component_code="C1")]))


def test_upsert_bom_unknown_component_raises(db_session):
    svc = _products(db_session)
    with pytest.raises(ValueError):
        svc.upsert_bom(BomUpsert(erp_ref="EB-4", product_code="FIN", items=[
            BomItemUpsert(component_code="NOPE")]))
