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
from lightmes.modules.production.models import SerialUnit, DefectType, DefectRecord
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _key(db_session):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username="defadm", password_hash=hash_password("p"),
             display_name="D", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="d-key", user_id=u.id, scopes=["read"])
    return full_key, u


def _env_with_defect(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="APVDP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="APVDL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="APVDW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="APVDR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="APVDRR", name="r", pattern="APVD{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APVDWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="APVD1", work_order_id=wo.id, product_id=p.id,
                    status="quarantined", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    dt = DefectType(code="TEST_DEFECT", name="测试缺陷", category="质量",
                    severity="major", is_active=True)
    db_session.add(dt); db_session.flush()
    # discovered_by 是 NOT NULL（DefectRecord.discovered_by: Mapped[int]），
    # 必须传一个真实 user id；创建一个 discoverer 用户。
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    discoverer = User(username="apvddisc", password_hash=hash_password("p"),
                      display_name="Disc", is_active=True, role_id=role.id)
    db_session.add(discoverer); db_session.flush()
    d = DefectRecord(
        defect_type_id=dt.id, defect_type_code=dt.code, defect_type_name=dt.name,
        severity=dt.severity, serial_unit_id=su.id, work_order_id=wo.id,
        operation_id=None, work_station_id=None, position=None,
        discovered_by=discoverer.id, handling_status="pending",
    )
    db_session.add(d); db_session.flush()
    return wo, d


def test_defects_list(client, db_session):
    wo, d = _env_with_defect(db_session)
    key, _u = _key(db_session)
    resp = client.get("/api/v1/defects",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["defect_type_code"] == "TEST_DEFECT"


def test_defects_list_filter_by_severity(client, db_session):
    wo, d = _env_with_defect(db_session)
    key, _u = _key(db_session)
    resp = client.get("/api/v1/defects?severity=major",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert all(d["severity"] == "major" for d in resp.json())
    # filter by non-existent severity
    resp2 = client.get("/api/v1/defects?severity=critical",
                       headers={"Authorization": f"Bearer {key}"})
    assert len(resp2.json()) == 0


def test_defects_get_one(client, db_session):
    wo, d = _env_with_defect(db_session)
    key, _u = _key(db_session)
    resp = client.get(f"/api/v1/defects/{d.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == d.id
    assert data["handling_status"] == "pending"


def test_defects_get_one_not_found(client, db_session):
    key, _u = _key(db_session)
    resp = client.get("/api/v1/defects/99999",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
