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


def test_get_bom_items_by_consume_op_returns_matching_items(db_session):
    """get_bom_items_by_consume_op 返回 consume_at_operation_seq == op_seq 的 active BOM 行。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    from lightmes.modules.masterdata.query_service import MasterDataQueryService

    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="QF1", name="成品", type="finished"))
    c1 = md.create_product(ProductCreate(code="QC1", name="件1", type="component", track_mode="serial"))
    c2 = md.create_product(ProductCreate(code="QC2", name="件2", type="component", track_mode="serial"))
    c3 = md.create_product(ProductCreate(code="QC3", name="件3", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1, consume_at_operation_seq=2),
        BomItemCreate(component_product_id=c2.id, qty=1, consume_at_operation_seq=3),
        BomItemCreate(component_product_id=c3.id, qty=1),  # NULL = 兼容老行为
    ]))

    svc = MasterDataQueryService(db_session)
    op2_items = svc.get_bom_items_by_consume_op(fin.id, 2)
    op3_items = svc.get_bom_items_by_consume_op(fin.id, 3)
    op4_items = svc.get_bom_items_by_consume_op(fin.id, 4)

    assert {i.component_product_id for i in op2_items} == {c1.id}
    assert {i.component_product_id for i in op3_items} == {c2.id}
    assert op4_items == []  # 不返回 NULL 的项


def test_get_bom_items_by_consume_op_returns_empty_when_no_active_bom(db_session):
    """无 active BOM 时返回空列表。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import ProductCreate
    from lightmes.modules.masterdata.query_service import MasterDataQueryService

    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="QF2", name="成品", type="finished"))

    svc = MasterDataQueryService(db_session)
    assert svc.get_bom_items_by_consume_op(fin.id, 1) == []
