import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import SkillCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="sk", password="pw12345", display_name="Sk"))
    db_session.flush()
    client.post("/login", data={"username": "sk", "password": "pw12345"})


def test_skills_page_and_create(client, db_session):
    _login(client, db_session)
    assert client.get("/masterdata/skills").status_code == 200
    resp = client.post("/masterdata/skills", data={"code": "ASSY", "name": "装配", "max_level": "3", "description": ""})
    assert resp.status_code == 200 and "ASSY" in resp.text


def test_skills_create_requires_login(client, db_session):
    resp = client.post("/masterdata/skills", data={"code": "X", "name": "x", "max_level": "3", "description": ""})
    assert resp.status_code == 401


def test_operator_skills_page_and_upsert(client, db_session):
    _login(client, db_session)
    sk = SkillService(db_session)
    s = sk.create_skill(SkillCreate(code="SK", name="技能", max_level=3))
    db_session.flush()
    # 当前登录用户 sk 已存在；取其 id 通过页面下拉不便，直接用 service 侧已知：用 users list
    assert client.get("/masterdata/operator-skills").status_code == 200
    # 找到 sk 用户 id
    from lightmes.modules.auth.repository import UserRepository
    uid = UserRepository(db_session).get_by_username("sk").id
    resp = client.post("/masterdata/operator-skills", data={"user_id": str(uid), "skill_id": str(s.id), "level": "2"})
    assert resp.status_code == 200
    # upsert：再设一次更高等级，仍 200，不新增第二条
    resp2 = client.post("/masterdata/operator-skills", data={"user_id": str(uid), "skill_id": str(s.id), "level": "3"})
    assert resp2.status_code == 200
    assert len(sk.list_operator_skills()) == 1


def test_operator_skills_level_out_of_range_error(client, db_session):
    _login(client, db_session)
    sk = SkillService(db_session)
    s = sk.create_skill(SkillCreate(code="SK2", name="技能", max_level=3))
    db_session.flush()
    from lightmes.modules.auth.repository import UserRepository
    uid = UserRepository(db_session).get_by_username("sk").id
    resp = client.post("/masterdata/operator-skills", data={"user_id": str(uid), "skill_id": str(s.id), "level": "9"})
    assert resp.status_code == 200  # error_row 片段
    assert "越界" in resp.text
