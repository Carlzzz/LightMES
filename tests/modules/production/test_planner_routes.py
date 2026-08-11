"""Task 5: Planner weekly view route tests.

Brief specified `u.role = "admin"` and `?week=YYYY-Www`. Both need adapting:
- User model has no legacy `role` column → use Role-row pattern
  (mirror of tests/modules/production/test_shift_pages.py).
- Brief later revises week param to ISO date (`YYYY-MM-DD`); use that form.
"""
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as sa_select

from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import Role, User
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.service import AuthService
from lightmes.modules.masterdata.schemas import (
    LineCreate, OperationCreate, ProductCreate, RoutingCreate, WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.service import ProductionService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login_admin(client, db_session):
    """登录 admin 用户（Role-row pattern；User 无 legacy role 字符串列）。"""
    role = db_session.execute(
        sa_select(Role).where(Role.name == "admin")
    ).scalar_one_or_none()
    if role is None:
        role = Role(name="admin", display_name="管理员", is_system=True)
        db_session.add(role)
        db_session.flush()
    AuthService(db_session).create_user(UserCreate(
        username="planadm", password="pw12345", display_name="Adm"))
    u = db_session.query(User).filter(User.username == "planadm").one()
    u.role_id = role.id
    db_session.flush()
    client.post("/login", data={"username": "planadm", "password": "pw12345"})


def _env(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PLNP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PLNL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="PLNW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="PLNR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id,
                                    allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="PLNR1", name="r", pattern="PLN{SEQ:4}"))
    return p, line, r, rule


def test_planner_page_requires_login(client, db_session):
    resp = client.get("/production/planner", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_planner_weekly_view_renders(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="PLNWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    monday = date.today() - timedelta(days=date.today().weekday())
    resp = client.get(f"/production/planner?week={monday.isoformat()}")
    assert resp.status_code == 200
    assert "Planner" in resp.text or "排程" in resp.text
    assert "PLNWO" in resp.text  # 工单 code 出现在 backlog


def test_planner_default_week_is_current_when_no_param(client, db_session):
    _login_admin(client, db_session)
    _env(db_session)
    resp = client.get("/production/planner")
    assert resp.status_code == 200


def test_planner_daily_view_renders(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    from datetime import datetime
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="PLND", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    wo.planned_start = datetime(2026, 8, 11, 10, 0)
    wo.planned_end = datetime(2026, 8, 11, 14, 0)
    db_session.flush()
    resp = client.get("/production/planner/daily?date=2026-08-11")
    assert resp.status_code == 200
    assert "PLND" in resp.text
