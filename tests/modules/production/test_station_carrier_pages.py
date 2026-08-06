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
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="sc", password="pw12345", display_name="Sc"))
    db_session.flush()
    client.post("/login", data={"username": "sc", "password": "pw12345"})


def _setup(db_session, n_ops=2, qty=2, status_release=True):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id,
        line_id=line.id, qty=qty, sn_rule_id=rule.id))
    if status_release:
        prod.release_work_order(wo.id)
    db_session.flush()
    return ws, wo, line


def test_work_orders_endpoint_returns_options(client, db_session):
    ws, wo, line = _setup(db_session)
    _login(client, db_session)
    resp = client.get(f"/production/station/work-orders?work_station_id={ws[0].id}")
    assert resp.status_code == 200
    assert f'<option value="{wo.id}"' in resp.text
    assert "剩余" in resp.text  # 含剩余 pending 数


def test_work_orders_endpoint_filters_other_line(client, db_session):
    ws, wo, line = _setup(db_session)
    md = MasterDataService(db_session)
    other_line = md.create_line(LineCreate(code="OTH", name="别线"))
    other_ws = md.create_work_station(WorkStationCreate(code="OW", name="别站", line_id=other_line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    resp = client.get(f"/production/station/work-orders?work_station_id={other_ws.id}")
    assert resp.status_code == 200
    assert f'value="{wo.id}"' not in resp.text  # 异产线工单不在结果


def test_work_orders_requires_login(client, db_session):
    ws, wo, line = _setup(db_session)
    resp = client.get(f"/production/station/work-orders?work_station_id={ws[0].id}")
    assert resp.status_code == 401


def test_enter_first_station_carrier_binds_sn_no_pass(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id),
                             "scan": "PALLET-1"})
    assert resp.status_code == 200
    # 进入主界面（station_view.html）：渲染了工艺路径全景 + PASS 按钮
    assert "确认过站" in resp.text or "工艺路径" in resp.text
    # 关键：SN 已绑载体码，但无 OperationRecord（只绑不过站）
    su = SerialUnitRepository(db_session).get_active_by_carrier("PALLET-1")
    assert su is not None and su.status == "pending"
    assert OperationRecordRepository(db_session).list_by_serial_unit(su.id) == []


def test_enter_downstream_sn_loads_main(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    # 先在首站用载体码绑一件
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-1"})
    su = SerialUnitRepository(db_session).get_active_by_carrier("PALLET-1")
    # 直接手工把 su 推进到工序2 模拟"首工序已过"，后续站扫 SN 应能加载
    from lightmes.modules.production.models import SerialUnit
    db_session.execute(
        __import__("sqlalchemy").update(SerialUnit).where(SerialUnit.id == su.id)
        .values(current_operation_seq=1, status="in_process"))
    db_session.flush()
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[1].id),
                             "work_order_id": str(wo.id), "scan": su.sn})
    assert resp.status_code == 200
    assert "工艺路径" in resp.text or "确认过站" in resp.text


def test_enter_carrier_already_bound_blocks(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-DUP"})
    # 同一载体码再投一件 → 已绑拦截
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id), "scan": "PALLET-DUP"})
    assert resp.status_code == 200
    assert "✗" in resp.text and ("解绑" in resp.text or "已绑" in resp.text)


def test_enter_work_order_exhausted_blocks(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=1)
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-1"})
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id), "scan": "PALLET-2"})
    assert resp.status_code == 200 and "全部投产" in resp.text


def test_enter_requires_login(client, db_session):
    ws, wo, line = _setup(db_session)
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id), "scan": "X"})
    assert resp.status_code == 401
