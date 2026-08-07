import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.repository import OperationWorkStationRepository
from lightmes.modules.masterdata.schemas import (
    OperationCreate, ProductCreate, LineCreate, RoutingCreate, WorkStationCreate,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="rb", password="pw12345", display_name="Rb"))
    db_session.flush()
    client.post("/login", data={"username": "rb", "password": "pw12345"})


def test_routing_page_and_create(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RW", name="站", line_id=line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    assert client.get("/masterdata/routings").status_code == 200
    resp = client.post("/masterdata/routings", data={
        "code": "RT1", "name": "路线", "product_id": str(p.id),
        "op_seq": ["1", "2"], "op_code": ["OP1", ""],
        "op_name": ["上料", ""], "op_ws": [str(w.id), ""],  # 空行忽略
    })
    assert resp.status_code == 200
    assert "RT1" in resp.text or "保存" in resp.text or "成功" in resp.text


def test_routing_create_invalid_shows_error_fragment(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RPE", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RLE", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RWE", name="站", line_id=line.id, seq=1))
    md.create_routing(RoutingCreate(
        code="RTDUP", name="已有", product_id=p.id, operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w.id, allowed_work_station_ids=[w.id]),
        ]))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data={
        "code": "RTDUP", "name": "重复", "product_id": str(p.id),
        "op_seq": ["1"], "op_code": ["OP1"], "op_name": ["上料"], "op_ws": [str(w.id)],
    })
    assert resp.status_code == 200  # graceful, not 500
    assert "alert--danger" in resp.text
    assert "已存在" in resp.text


def test_routing_create_nonnumeric_seq_graceful(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RPN", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RLN", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RWN", name="站", line_id=line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data={
        "code": "RTN", "name": "非数字", "product_id": str(p.id),
        "op_seq": ["abc"], "op_code": ["OP1"], "op_name": ["上料"], "op_ws": [str(w.id)],
    })
    assert resp.status_code == 200  # graceful, not 500
    assert "alert--danger" in resp.text


def test_routing_create_allowed_multi(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RPM", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RLM", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="RWM1", name="站1", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(code="RWM2", name="站2", line_id=line.id, seq=2))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data={
        "code": "RTM", "name": "多站", "product_id": str(p.id),
        "op_seq": ["1"], "op_code": ["OP1"], "op_name": ["上料"],
        "op_ws": [str(w1.id)], "op_allowed": [f"{w1.id},{w2.id}"],
    })
    assert resp.status_code == 200
    routing = md.routings.get_by_code("RTM")
    op = md.routings.operations_of(routing.id)[0]
    allowed = OperationWorkStationRepository(db_session).list_by_operation(op.id)
    assert {a.work_station_id for a in allowed} == {w1.id, w2.id}


def test_routing_create_allowed_dedup(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RPD", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RLD", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="RWD1", name="站1", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(code="RWD2", name="站2", line_id=line.id, seq=2))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data={
        "code": "RTD", "name": "去重", "product_id": str(p.id),
        "op_seq": ["1"], "op_code": ["OP1"], "op_name": ["上料"],
        "op_ws": [str(w1.id)], "op_allowed": [f"{w1.id},{w1.id},{w2.id}"],
    })
    assert resp.status_code == 200
    routing = md.routings.get_by_code("RTD")
    op = md.routings.operations_of(routing.id)[0]
    allowed = OperationWorkStationRepository(db_session).list_by_operation(op.id)
    assert {a.work_station_id for a in allowed} == {w1.id, w2.id}


def test_api_routing_read_includes_allowed(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RPA", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RLA", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="RWA1", name="站1", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(code="RWA2", name="站2", line_id=line.id, seq=2))
    md.create_routing(RoutingCreate(code="RTA", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=1, code="OP1", name="装配",
                        default_work_station_id=w1.id,
                        allowed_work_station_ids=[w1.id, w2.id])]))
    db_session.flush()
    routing = md.routings.get_by_code("RTA")
    resp = client.get(f"/api/masterdata/routings/{routing.id}")
    assert resp.status_code == 200
    rbody = resp.json()
    assert rbody["code"] == "RTA"
    op = rbody["operations"][0]
    assert set(op["allowed_work_station_ids"]) == {w1.id, w2.id}


def test_products_page_shows_source_badge(client, db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="BADGE-M", name="本地件", type="component"))
    _login(client, db_session)
    resp = client.get("/masterdata/products")
    assert resp.status_code == 200
    assert "本地" in resp.text  # 来源徽标


def test_boms_page_renders(client, db_session):
    _login(client, db_session)
    assert client.get("/masterdata/boms").status_code == 200
