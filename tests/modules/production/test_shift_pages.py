import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.models import Role, User
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login_admin(client, db_session):
    """登录 admin 用户以满足 _can_skip(user) 守卫。

    User 模型无 legacy `role` 字符串列，需通过 Role 表 + role_id 走 role_obj 关系。
    （brief 指定的 u.role="admin" 因 User 无此列而无法工作，使用项目既有的
    test_routing_bom_pages._login_admin 同款模式替代。）
    """
    from sqlalchemy import select as sa_select
    role = db_session.execute(sa_select(Role).where(Role.name == "admin")).scalar_one_or_none()
    if role is None:
        role = Role(name="admin", display_name="管理员", is_system=True)
        db_session.add(role)
        db_session.flush()
    u = User(username="shiftadm",
             password_hash=hash_password("pw12345"),
             display_name="Adm",
             role_id=role.id)
    db_session.add(u)
    db_session.flush()
    client.post("/login", data={"username": "shiftadm", "password": "pw12345"})


def test_shifts_page_requires_login(client, db_session):
    resp = client.get("/production/shifts", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_shifts_page_renders_for_admin(client, db_session):
    _login_admin(client, db_session)
    resp = client.get("/production/shifts")
    assert resp.status_code == 200
    assert "班次" in resp.text


def test_shift_create_via_post(client, db_session):
    _login_admin(client, db_session)
    resp = client.post("/production/shifts", data={
        "code": "P1", "name": "早班", "start_time": "06:00", "end_time": "14:00",
        "days_of_week": "1,2,3,4,5", "sort_order": "1",
    })
    assert resp.status_code in (200, 303)
    from lightmes.modules.production.models import Shift
    s = db_session.query(Shift).filter(Shift.code == "P1").one()
    assert s.name == "早班"
