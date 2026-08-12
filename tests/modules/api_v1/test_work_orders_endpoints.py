from datetime import datetime
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
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env(db_session):
    """Product + routing + line + sn_rule."""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="APV1P", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="APV1L", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="APV1W", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="APV1R", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="APV1RR", name="r", pattern="APV{SEQ:4}"))
    return p, line, r, rule


def _admin_key(db_session, username="woadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="woadm-key", user_id=u.id, scopes=["read", "write"])
    return full_key


def _ro_key(db_session, username="ro_u"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="RO", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="ro-key", user_id=u.id, scopes=["read"])
    return full_key


def test_work_orders_list_pagination(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    svc = ProductionService(db_session)
    for i in range(5):
        svc.create_work_order(WorkOrderCreate(
            code=f"APV1W{i}", product_id=p.id, routing_id=r.id, line_id=line.id,
            qty=10, sn_rule_id=rule.id))
    resp = client.get("/api/v1/work-orders?page=1&size=3",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "5"
    assert resp.headers["X-Page"] == "1"
    assert resp.headers["X-Size"] == "3"
    data = resp.json()
    assert len(data) == 3


def test_work_orders_list_filter_by_status(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    svc = ProductionService(db_session)
    wo = svc.create_work_order(WorkOrderCreate(
        code="APV1S1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    svc.release_work_order(wo.id)  # released
    svc.create_work_order(WorkOrderCreate(
        code="APV1S2", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))  # created
    resp = client.get("/api/v1/work-orders?status=released",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["code"] == "APV1S1"


def test_work_orders_get_one(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APV1G1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    resp = client.get(f"/api/v1/work-orders/{wo.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == wo.id
    assert data["code"] == "APV1G1"
    assert "priority" in data
    assert "process_snapshot" not in data  # 内部字段不暴露


def test_work_orders_get_one_not_found_returns_problem_details(client, db_session):
    key = _admin_key(db_session)
    resp = client.get("/api/v1/work-orders/99999",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_work_orders_create_success(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    resp = client.post("/api/v1/work-orders",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"code": "APV1C1", "product_id": p.id,
                             "routing_id": r.id, "line_id": line.id,
                             "qty": 50, "sn_rule_id": rule.id, "priority": 7})
    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "APV1C1"
    assert data["priority"] == 7


def test_work_orders_create_readonly_key_forbidden(client, db_session):
    p, line, r, rule = _env(db_session)
    ro_key = _ro_key(db_session)
    resp = client.post("/api/v1/work-orders",
                       headers={"Authorization": f"Bearer {ro_key}"},
                       json={"code": "APV1C2", "product_id": p.id,
                             "routing_id": r.id, "line_id": line.id,
                             "qty": 50, "sn_rule_id": rule.id})
    assert resp.status_code == 403


def test_work_orders_patch_priority(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APV1P1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    resp = client.patch(f"/api/v1/work-orders/{wo.id}/priority",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"priority": 9})
    assert resp.status_code == 200
    assert resp.json()["priority"] == 9
