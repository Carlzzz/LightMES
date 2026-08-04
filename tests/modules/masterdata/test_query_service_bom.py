from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, BomCreate, BomItemCreate,
)


def _fixture(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="QF", name="成品", type="finished"))
    c1 = md.create_product(
        ProductCreate(code="QC1", name="主板", type="component", track_mode="serial"))
    c2 = md.create_product(
        ProductCreate(code="QC2", name="螺丝", type="consumable", track_mode="batch"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1),
        BomItemCreate(component_product_id=c2.id, qty=4),
    ]))
    return fin, c1, c2


def test_get_active_bom(db_session):
    fin, c1, c2 = _fixture(db_session)
    q = MasterDataQueryService(db_session)
    bom = q.get_active_bom(fin.id)
    assert bom is not None
    assert bom.status == "active"


def test_get_active_bom_items(db_session):
    fin, c1, c2 = _fixture(db_session)
    q = MasterDataQueryService(db_session)
    items = q.get_active_bom_items(fin.id)
    comp_ids = {i.component_product_id for i in items}
    assert comp_ids == {c1.id, c2.id}


def test_get_active_bom_items_empty_for_no_bom(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="NOBOM", name="x", type="finished"))
    q = MasterDataQueryService(db_session)
    assert q.get_active_bom(p.id) is None
    assert q.get_active_bom_items(p.id) == []
