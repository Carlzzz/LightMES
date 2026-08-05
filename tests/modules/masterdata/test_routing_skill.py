import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, SkillCreate,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="rs", password="pw12345", display_name="Rs"))
    db_session.flush()
    client.post("/login", data={"username": "rs", "password": "pw12345"})


def test_routing_create_with_skill_requirement(client, db_session):
    md = MasterDataService(db_session); sk = SkillService(db_session)
    p = md.create_product(ProductCreate(code="RP", name="件", type="finished"))
    line = md.create_line(LineCreate(code="RL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RW", name="站", line_id=line.id, seq=1))
    s = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data={
        "code": "RT1", "name": "路线", "product_id": str(p.id),
        "op_seq": "1", "op_code": "OP1", "op_name": "上料", "op_ws": str(w.id),
        "op_skill": str(s.id), "op_level": "2",
    })
    assert resp.status_code == 200
    routing = md.routings.get_by_code("RT1")
    op = md.routings.operations_of(routing.id)[0]
    assert op.required_skill_id == s.id and op.required_level == 2


def test_routing_create_without_skill_leaves_null(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RP2", name="件", type="finished"))
    line = md.create_line(LineCreate(code="RL2", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RW2", name="站", line_id=line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data={
        "code": "RT2", "name": "路线", "product_id": str(p.id),
        "op_seq": "1", "op_code": "OP1", "op_name": "上料", "op_ws": str(w.id),
        "op_skill": "", "op_level": "",  # 无技能要求
    })
    assert resp.status_code == 200
    routing = md.routings.get_by_code("RT2")
    op = md.routings.operations_of(routing.id)[0]
    assert op.required_skill_id is None and op.required_level is None
