import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env_with_sn(db_session, sn="APVSN1"):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="APVSNP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="APSNL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="APSNW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="APSNR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="APSNRR", name="r", pattern="APSN{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APSNWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn=sn, work_order_id=wo.id, product_id=p.id, status="in_process",
                    current_operation_seq=2)
    db_session.add(su); db_session.flush()
    return wo, su


def _key(db_session, scopes=None, username="snadm"):
    scopes = scopes or ["read", "write"]
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="sn-key", user_id=u.id, scopes=scopes)
    return full_key


def test_serial_units_list(client, db_session):
    wo, su = _env_with_sn(db_session)
    key = _key(db_session)
    resp = client.get("/api/v1/serial-units",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["sn"] == "APVSN1"


def test_serial_units_list_filter_by_work_order(client, db_session):
    wo, su = _env_with_sn(db_session)
    key = _key(db_session)
    resp = client.get(f"/api/v1/serial-units?work_order_id={wo.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    # 不存在的 work_order_id
    resp2 = client.get("/api/v1/serial-units?work_order_id=99999",
                       headers={"Authorization": f"Bearer {key}"})
    assert len(resp2.json()) == 0


def test_serial_units_get_one(client, db_session):
    wo, su = _env_with_sn(db_session)
    key = _key(db_session)
    resp = client.get(f"/api/v1/serial-units/{su.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == su.id
    assert data["current_operation_seq"] == 2
    assert data["status"] == "in_process"


def test_serial_units_by_sn(client, db_session):
    wo, su = _env_with_sn(db_session, sn="APVSNSPEC")
    key = _key(db_session)
    resp = client.get("/api/v1/serial-units/by-sn/APVSNSPEC",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.json()["sn"] == "APVSNSPEC"


def test_serial_units_by_sn_not_found(client, db_session):
    key = _key(db_session)
    resp = client.get("/api/v1/serial-units/by-sn/NOSUCHSN",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
