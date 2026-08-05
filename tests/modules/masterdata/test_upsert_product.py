from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductUpsert, ProductCreate


def test_upsert_creates_new_erp_product(db_session):
    svc = MasterDataService(db_session)
    obj, action = svc.upsert_product(ProductUpsert(
        erp_ref="ERP-1", code="P-1", name="件A", type="component"))
    assert action == "created"
    assert obj.source == "erp"
    assert obj.erp_ref == "ERP-1"
    assert obj.synced_at is not None


def test_upsert_updates_existing_by_erp_ref(db_session):
    svc = MasterDataService(db_session)
    svc.upsert_product(ProductUpsert(erp_ref="ERP-2", code="P-2", name="旧名", type="component"))
    obj, action = svc.upsert_product(ProductUpsert(
        erp_ref="ERP-2", code="P-2", name="新名", type="component"))
    assert action == "updated"
    assert obj.name == "新名"


def test_upsert_idempotent(db_session):
    svc = MasterDataService(db_session)
    o1, a1 = svc.upsert_product(ProductUpsert(erp_ref="ERP-3", code="P-3", name="x", type="component"))
    o2, a2 = svc.upsert_product(ProductUpsert(erp_ref="ERP-3", code="P-3", name="x", type="component"))
    assert a1 == "created" and a2 == "updated"
    assert o1.id == o2.id  # 同一条，不重复


def test_upsert_does_not_touch_manual_product(db_session):
    svc = MasterDataService(db_session)
    # 手动建一个 code=SHARED 的 manual 产品（erp_ref 空）
    manual = svc.create_product(ProductCreate(code="SHARED", name="本地", type="component"))
    # ERP 导入一个不同 erp_ref 的产品（即使 code 相似也按 erp_ref 匹配，不会命中 manual）
    obj, action = svc.upsert_product(ProductUpsert(
        erp_ref="ERP-9", code="ERP-CODE", name="ERP件", type="component"))
    assert action == "created"
    # manual 未被改动
    assert svc.products.get(manual.id).source == "manual"
    assert svc.products.get(manual.id).name == "本地"
