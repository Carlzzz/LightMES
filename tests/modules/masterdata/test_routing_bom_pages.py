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
    BomCreate, BomItemCreate,
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


def test_routing_create_modal_uses_checkbox_picker(client, db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="RPUI", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RLUI", name="线"))
    md.create_work_station(WorkStationCreate(code="RWUI", name="站", line_id=line.id, seq=1))
    db_session.flush()
    _login(client, db_session)

    resp = client.get("/masterdata/routings")

    assert resp.status_code == 200
    assert 'id="st_allowed" class="st-allowed-list"' in resp.text
    assert "st-allowed-cb" in resp.text
    assert '<select id="st_allowed"' not in resp.text
    assert "selectedOptions" not in resp.text


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


def _login_admin(client, db_session):
    """登录一个 admin 用户（满足 require_role("admin","supervisor")）。

    通过 Role 表 + user.role_id 设置（User 模型无 legacy `role` 字符串列，
    只能走 role_obj 关系路径触发 require_role 通过）。
    """
    from sqlalchemy import select as sa_select
    from lightmes.modules.auth.models import Role, User as U
    from lightmes.shared.security import hash_password

    role = db_session.execute(sa_select(Role).where(Role.name == "admin")).scalar_one_or_none()
    if role is None:
        role = Role(name="admin", display_name="管理员", is_system=True)
        db_session.add(role)
        db_session.flush()
    u = U(username="admbom", password_hash=hash_password("pw12345"),
          display_name="Adm", role_id=role.id)
    db_session.add(u)
    db_session.flush()
    client.post("/login", data={"username": "admbom", "password": "pw12345"})


def _bom_for_patch(db_session):
    """构造 product + active routing (3 ops) + active BOM (1 item)。返回 (bom, item, op_seqs)。"""
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="PBF", name="成品", type="finished"))
    c1 = md.create_product(ProductCreate(code="PBC", name="件", type="component", track_mode="serial"))
    line = md.create_line(LineCreate(code="PBL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="PBW", name="站", line_id=line.id, seq=1))
    md.create_routing(RoutingCreate(
        code="PBR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=i, code=f"OP{i}", name=f"工序{i}",
                                    default_work_station_id=w.id,
                                    allowed_work_station_ids=[w.id])
                    for i in range(1, 4)]))
    bom = md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1)]))
    items = md.boms.items_of(bom.id)
    return bom, items[0], [1, 2, 3]


def test_patch_bom_item_consume_op_updates_field(db_session, client):
    """PATCH /api/masterdata/bom-items/{id}/consume-op 更新 consume_at_operation_seq 成功。"""
    bom, item, _ = _bom_for_patch(db_session)
    db_session.flush()
    _login_admin(client, db_session)
    resp = client.patch(f"/api/masterdata/bom-items/{item.id}/consume-op",
                        json={"consume_at_operation_seq": 2})
    assert resp.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(type(item), item.id)
    assert refreshed.consume_at_operation_seq == 2


def test_patch_bom_item_consume_op_rejects_invalid_seq(db_session, client):
    """PATCH 用不属于 routing 的 seq → 400。"""
    bom, item, _ = _bom_for_patch(db_session)
    db_session.flush()
    _login_admin(client, db_session)
    resp = client.patch(f"/api/masterdata/bom-items/{item.id}/consume-op",
                        json={"consume_at_operation_seq": 99})
    assert resp.status_code == 400
    assert "不属于" in resp.text or "Routing" in resp.text


def test_patch_bom_item_consume_op_clears_with_null(db_session, client):
    """PATCH 用 null 清空 consume_at_operation_seq（回退到兼容老行为）。"""
    bom, item, _ = _bom_for_patch(db_session)
    item.consume_at_operation_seq = 2  # 预置
    db_session.flush()
    _login_admin(client, db_session)
    resp = client.patch(f"/api/masterdata/bom-items/{item.id}/consume-op",
                        json={"consume_at_operation_seq": None})
    assert resp.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(type(item), item.id)
    assert refreshed.consume_at_operation_seq is None


def test_bom_detail_page_requires_login(client, db_session):
    """未登录访问 /masterdata/boms/{id} 返回 401。"""
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BDTF", name="成品", type="finished"))
    c1 = md.create_product(ProductCreate(code="BDTC", name="件", type="component", track_mode="serial"))
    bom = md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1)]))
    db_session.flush()
    resp = client.get(f"/masterdata/boms/{bom.id}")
    assert resp.status_code == 401


def test_bom_detail_page_accessible_when_logged_in(client, db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BDTG", name="成品", type="finished"))
    c1 = md.create_product(ProductCreate(code="BDTH", name="件", type="component", track_mode="serial"))
    bom = md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1)]))
    db_session.flush()
    _login(client, db_session)
    resp = client.get(f"/masterdata/boms/{bom.id}")
    assert resp.status_code == 200
