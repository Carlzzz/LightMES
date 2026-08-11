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


def test_upsert_bom_product_change_rejected(db_session):
    svc = _products(db_session)
    svc.create_product(ProductCreate(code="FIN2", name="成品2", type="finished"))
    svc.upsert_bom(BomUpsert(erp_ref="EB-X", product_code="FIN", items=[
        BomItemUpsert(component_code="C1", qty=1)]))
    # 同 erp_ref 换成品：不静默改挂产品，直接拒绝
    with pytest.raises(ValueError, match="成品与已存在记录不一致"):
        svc.upsert_bom(BomUpsert(erp_ref="EB-X", product_code="FIN2", items=[
            BomItemUpsert(component_code="C1", qty=1)]))


def test_create_bom_persists_consume_at_operation_seq(db_session):
    """create_bom 透传 consume_at_operation_seq 到 BomItem。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN2", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1B", name="件", type="component", track_mode="serial"))
    bom = md.create_bom(BomCreate(product_id=md.products.get_by_code("FIN2").id, items=[
        BomItemCreate(component_product_id=md.products.get_by_code("C1B").id, qty=1,
                      consume_at_operation_seq=3),
    ]))
    items = md.boms.items_of(bom.id)
    assert items[0].consume_at_operation_seq == 3


def test_create_bom_consume_op_defaults_none(db_session):
    """不传 consume_at_operation_seq 时默认 None（兼容老行为）。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN3", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1C", name="件", type="component", track_mode="serial"))
    bom = md.create_bom(BomCreate(product_id=md.products.get_by_code("FIN3").id, items=[
        BomItemCreate(component_product_id=md.products.get_by_code("C1C").id, qty=1),
    ]))
    items = md.boms.items_of(bom.id)
    assert items[0].consume_at_operation_seq is None
