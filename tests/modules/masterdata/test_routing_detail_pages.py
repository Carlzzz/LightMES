import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="ed", password="pw12345", display_name="Ed"))
    db_session.flush()
    client.post("/login", data={"username": "ed", "password": "pw12345"})


def _setup(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
    ]))
    db_session.flush()
    return routing, (ws1, ws2)


def test_detail_page_renders(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.get(f"/masterdata/routings/{routing.id}")
    assert resp.status_code == 200
    assert "RT" in resp.text and "工序10" in resp.text
    assert "路线头" in resp.text or "RT" in resp.text  # 路线头卡片渲染
    assert "默认作业站" in resp.text  # 工序表格头
    assert "删除路线" in resp.text  # 危险按钮


def test_detail_requires_login(client, db_session):
    routing, wss = _setup(db_session)
    resp = client.get(f"/masterdata/routings/{routing.id}")
    assert resp.status_code == 401


def test_update_head_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}", data={"name": "新名"})
    assert resp.status_code == 200
    db_session.refresh(routing)
    assert routing.name == "新名"


def test_set_status_active(client, db_session):
    routing, wss = _setup(db_session)
    md = MasterDataService(db_session)
    md.set_routing_status(routing.id, "inactive")  # 先 inactive
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "active"})
    assert resp.status_code == 200
    db_session.refresh(routing)
    assert routing.status == "active"


def test_add_operation_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}/operations",
                       data={"seq": "20", "code": "OP20", "name": "工序20",
                             "op_ws": str(wss[1].id),
                             "op_allowed": str(wss[1].id)})
    assert resp.status_code == 200
    assert "工序20" in resp.text


def test_update_operation_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    op = MasterDataService(db_session).routings.operations_of(routing.id)[0]
    resp = client.post(f"/masterdata/routings/{routing.id}/operations/{op.id}",
                       data={"seq": "15", "code": "OP15", "name": "改名",
                             "op_ws": str(wss[0].id),
                             "op_allowed": f"{wss[0].id},{wss[1].id}"})
    assert resp.status_code == 200
    db_session.refresh(op)
    assert op.name == "改名" and op.seq == 15


def test_delete_operation_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    op = MasterDataService(db_session).routings.operations_of(routing.id)[0]
    resp = client.post(f"/masterdata/routings/{routing.id}/operations/{op.id}/delete")
    assert resp.status_code == 200
    assert MasterDataService(db_session).routings.operations_of(routing.id) == []


def test_delete_routing_redirects_to_list(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/masterdata/routings"
