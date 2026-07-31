import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate


def test_create_product_persists(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(
        ProductCreate(code="NBK-A", name="外壳A", type="finished", unit="pcs")
    )
    assert p.id is not None
    assert p.code == "NBK-A"
    assert p.track_mode == "none"


def test_create_product_duplicate_code_rejected(db_session):
    svc = MasterDataService(db_session)
    svc.create_product(ProductCreate(code="DUP", name="x", type="component"))
    with pytest.raises(ValueError):
        svc.create_product(ProductCreate(code="DUP", name="y", type="component"))
