from lightmes.modules.integration.service import FileErpSyncService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate


PRODUCT_CSV = b"""erp_ref,code,name,type,spec,unit,track_mode
ERP-P1,P-1,\xe4\xbb\xb6A,component,,pcs,serial
ERP-P2,P-2,\xe4\xbb\xb6B,component,,pcs,batch
"""

def test_sync_products_csv_created_then_idempotent(db_session):
    svc = FileErpSyncService(db_session)
    r1 = svc.sync_products(PRODUCT_CSV)
    assert r1.created == 2 and r1.updated == 0 and not r1.errors
    r2 = svc.sync_products(PRODUCT_CSV)  # 重复导入
    assert r2.created == 0 and r2.updated == 2  # 幂等：全部 updated

def test_sync_products_bad_row_partial_success(db_session):
    bad = b"erp_ref,code,name,type\nERP-A,A,\xe5\xa5\xbd,component\n,,,\n"  # 第2行缺 erp_ref
    svc = FileErpSyncService(db_session)
    r = svc.sync_products(bad)
    assert r.created == 1
    assert r.skipped == 1 and len(r.errors) == 1  # 坏行跳过，好行照常

def test_sync_boms_json(db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1", name="主板", type="component", track_mode="serial"))
    import json
    payload = json.dumps([{"erp_ref": "EB-1", "product_code": "FIN",
        "items": [{"component_code": "C1", "qty": 1}]}]).encode()
    r = FileErpSyncService(db_session).sync_boms(payload)
    assert r.created == 1 and not r.errors

def test_sync_boms_unknown_component_partial(db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN2", name="成品", type="finished"))
    import json
    payload = json.dumps([{"erp_ref": "EB-2", "product_code": "FIN2",
        "items": [{"component_code": "NOPE", "qty": 1}]}]).encode()
    r = FileErpSyncService(db_session).sync_boms(payload)
    assert r.created == 0 and r.skipped == 1 and len(r.errors) == 1

def test_sync_boms_non_list_json_reported(db_session):
    svc = FileErpSyncService(db_session)
    # dict：本应是非法的 BOM 数组，但 JSON 合法，不应抛异常
    r = svc.sync_boms(b'{"erp_ref": "X"}')
    assert r.created == 0 and len(r.errors) == 1
    # null：同样是合法 JSON 但不是数组
    r2 = svc.sync_boms(b'null')
    assert r2.created == 0 and len(r2.errors) == 1


def test_sync_boms_dup_component_partial(db_session):
    """flush 级失败（uq_bom_item_component 重复组件）只回滚该条，不毒化整批。"""
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN3", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1", name="主板", type="component", track_mode="serial"))
    import json
    payload = json.dumps([
        {"erp_ref": "EB-DUP", "product_code": "FIN3",
         "items": [{"component_code": "C1", "qty": 1},
                   {"component_code": "C1", "qty": 2}]},  # 同组件两行 → flush IntegrityError
        {"erp_ref": "EB-OK", "product_code": "FIN3",
         "items": [{"component_code": "C1", "qty": 1}]},  # 合法，应创建
    ]).encode()
    r = FileErpSyncService(db_session).sync_boms(payload)
    assert r.created == 1
    assert r.skipped == 1 and len(r.errors) == 1  # dup 跳过、OK 创建、无异常传播
    assert md.boms.get_by_erp_ref("EB-OK") is not None
    assert md.boms.get_by_erp_ref("EB-DUP") is None


def test_sync_products_code_collision_partial(db_session):
    """flush 级失败（products.code 唯一冲突）只回滚该行，后续行照常创建。"""
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="DUP", name="已存在", type="component"))
    csv = b"erp_ref,code,name,type\nE1,DUP,\xe7\xa2\xb0\xe6\x92\x9e,component\nE2,FRESH,\xe6\x96\xb0,component\n"
    r = FileErpSyncService(db_session).sync_products(csv)
    assert r.created == 1
    assert r.skipped == 1 and len(r.errors) == 1  # DUP 冲突跳过、FRESH 创建、无异常传播
    assert md.products.get_by_code("FRESH") is not None
