import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate,
    LineCreate,
    WorkStationCreate,
    RoutingCreate,
    OperationCreate,
    BomCreate,
    BomItemCreate,
)
from lightmes.modules.masterdata.models import Product


def test_product_defaults_source_manual(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="ERP-P1", name="件", type="component"))
    assert p.source == "manual"
    assert p.erp_ref is None
    assert p.synced_at is None


def test_erp_ref_partial_unique(db_session):
    # 两条相同 erp_ref 的 product → 违反部分唯一索引
    db_session.add(Product(code="E1", name="a", type="component", source="erp", erp_ref="ERP-X"))
    db_session.flush()
    db_session.add(Product(code="E2", name="b", type="component", source="erp", erp_ref="ERP-X"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_null_erp_ref_not_constrained(db_session):
    # 多条 erp_ref=None（manual）互不冲突
    svc = MasterDataService(db_session)
    svc.create_product(ProductCreate(code="M1", name="a", type="component"))
    svc.create_product(ProductCreate(code="M2", name="b", type="component"))
    # 无异常即通过


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_routing_bom_responses_include_erp_fields(client, db_session):
    # 手工构造 RoutingRead/BomRead 的 API 路由必须带上新字段
    auth = AuthService(db_session)
    auth.initialize_default_roles()
    admin_role = auth.role_repo.get_by_name("admin")
    auth.create_user(
        UserCreate(username="api", password="pw12345", display_name="Api", role_id=admin_role.id))
    db_session.flush()
    r = client.post("/login", data={"username": "api", "password": "pw12345"})
    assert r.status_code == 204

    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="API-ERP", name="成品", type="finished"))
    comp = svc.create_product(
        ProductCreate(code="API-ERP-C", name="件", type="component"))
    line = svc.create_line(LineCreate(code="API-L", name="线"))
    ws = svc.create_work_station(WorkStationCreate(
        code="API-W", name="站", line_id=line.id, seq=1))

    rr = client.post("/api/masterdata/routings", json={
        "code": "API-R", "name": "路线", "product_id": p.id,
        "operations": [{"seq": 1, "code": "OP1", "name": "装配",
                        "default_work_station_id": ws.id,
                        "allowed_work_station_ids": [ws.id], "is_mandatory": True}],
    })
    assert rr.status_code == 201, rr.text
    rbody = rr.json()
    assert rbody["source"] == "manual"
    assert rbody["erp_ref"] is None
    assert rbody["synced_at"] is None

    br = client.post("/api/masterdata/boms", json={
        "product_id": p.id,
        "items": [{"component_product_id": comp.id, "qty": 1}],
    })
    assert br.status_code == 201, br.text
    bbody = br.json()
    assert bbody["source"] == "manual"
    assert bbody["erp_ref"] is None
    assert bbody["synced_at"] is None
