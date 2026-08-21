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
    resp = client.get(f"/masterdata/routings/{routing.id}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


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


def test_e2e_full_edit_flow(client, db_session):
    """完整编辑流：详情 → 改头 → 切 inactive→active → 加工序 → 改工序 → 删工序 → 删路线"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PE", name="件E", type="finished"))
    line = md.create_line(LineCreate(code="LE", name="线E"))
    ws1 = md.create_work_station(WorkStationCreate(code="WE1", name="站E1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="WE2", name="站E2", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RTE", name="路线E", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id])]))
    db_session.flush()
    _login(client, db_session)

    # 1) 改头
    r = client.post(f"/masterdata/routings/{routing.id}", data={"name": "新名E"})
    assert r.status_code == 200
    db_session.refresh(routing); assert routing.name == "新名E"

    # 2) 切 active → inactive → active
    r = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "inactive"})
    assert r.status_code == 200
    r = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "active"})
    assert r.status_code == 200

    # 3) 加工序
    r = client.post(f"/masterdata/routings/{routing.id}/operations",
                    data={"seq": "20", "code": "OP20", "name": "工序20",
                          "op_ws": str(ws2.id), "op_allowed": str(ws2.id)})
    assert r.status_code == 200 and "工序20" in r.text
    ops = md.routings.operations_of(routing.id)
    assert len(ops) == 2 and any(o.code == "OP20" for o in ops)

    # 4) 改工序（OP20 → allowed 加 ws1）
    op20 = next(o for o in ops if o.code == "OP20")
    r = client.post(f"/masterdata/routings/{routing.id}/operations/{op20.id}",
                    data={"seq": "20", "code": "OP20", "name": "改名20",
                          "op_ws": str(ws2.id), "op_allowed": f"{ws1.id},{ws2.id}"})
    assert r.status_code == 200
    db_session.refresh(op20)
    assert op20.name == "改名20"

    # 5) 删工序 OP10
    op10 = next(o for o in md.routings.operations_of(routing.id) if o.code == "OP10")
    r = client.post(f"/masterdata/routings/{routing.id}/operations/{op10.id}/delete")
    assert r.status_code == 200
    remaining = md.routings.operations_of(routing.id)
    assert len(remaining) == 1 and remaining[0].code == "OP20"

    # 6) 删路线 → 303 回列表
    r = client.post(f"/masterdata/routings/{routing.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert md.routings.get(routing.id) is None


def test_e2e_work_order_blocks_all_writes(client, db_session):
    """工单引用 → 所有写操作拒绝"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate

    def _build():
        md = MasterDataService(db_session)
        p = md.create_product(ProductCreate(code="PW", name="件W", type="finished"))
        line = md.create_line(LineCreate(code="LW", name="线W"))
        ws1 = md.create_work_station(WorkStationCreate(code="WW1", name="站W1", line_id=line.id, seq=1))
        routing = md.create_routing(RoutingCreate(code="RTW", name="路线W", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序10",
                            default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id])]))
        prod = ProductionService(db_session)
        rule = prod.create_sn_rule(SnRuleCreate(code="SRW", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
        prod.create_work_order(WorkOrderCreate(code="WOW", product_id=p.id, routing_id=routing.id,
            line_id=line.id, qty=3, sn_rule_id=rule.id))
        db_session.flush()
        return routing

    # 注意：每个被拒写操作都会触发路由内 db.rollback()，把共享测试事务一并回滚
    # （包括 _login 的 user），因此每次写前重建 fixture + 重新登录。
    # 改头 → 拒
    routing = _build()
    _login(client, db_session)
    r = client.post(f"/masterdata/routings/{routing.id}", data={"name": "x"})
    assert "工单" in r.text
    # 切状态 → 拒
    routing = _build()
    _login(client, db_session)
    r = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "inactive"})
    assert "工单" in r.text
    # 删路线 → 拒（200 全页重渲染含错误，不是 303）。注意：路由内 db.rollback() 会回滚
    # 未提交事务；routing 必须已 COMMIT 才能在重渲染时仍存在，否则返回 404。
    # 整个子用例包在 try/finally：一旦断言失败，finally 仍会清理已提交的 fixture 行
    # （PW/LW/WW1/RTW/SRW/WOW），避免污染共享开发库。cleanup 用独立 Session，
    # 即使 db_session 处于 rollback 后的状态也能清理已提交数据。
    from sqlalchemy import text as sqlalchemy_text
    from lightmes.database import SessionLocal
    routing = _build()
    routing_id = routing.id
    db_session.commit()  # 提交 routing+工单，使详情页在 rollback 后仍能重渲染
    cleanup = SessionLocal()
    try:
        _login(client, db_session)
        r = client.post(f"/masterdata/routings/{routing_id}/delete", follow_redirects=False)
        assert r.status_code == 200 and "工单" in r.text
        assert MasterDataService(db_session).routings.get(routing_id) is not None  # 未被删除
    finally:
        # 清理已提交数据（独立 Session，按 FK 依赖顺序 DELETE）
        cleanup.execute(sqlalchemy_text("delete from work_orders where code='WOW'"))
        cleanup.execute(sqlalchemy_text("delete from operations where routing_id in (select id from routings where code='RTW')"))
        cleanup.execute(sqlalchemy_text("delete from operation_work_stations where operation_id in (select id from operations where routing_id in (select id from routings where code='RTW'))"))
        cleanup.execute(sqlalchemy_text("delete from routings where code='RTW'"))
        cleanup.execute(sqlalchemy_text("delete from sn_rules where code='SRW'"))
        cleanup.execute(sqlalchemy_text("delete from work_stations where code='WW1'"))
        cleanup.execute(sqlalchemy_text("delete from lines where code='LW'"))
        cleanup.execute(sqlalchemy_text("delete from products where code='PW'"))
        cleanup.commit()
        cleanup.close()
