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
    AuthService(db_session).create_user(UserCreate(username="mf", password="pw12345", display_name="Mf"))
    db_session.flush()
    client.post("/login", data={"username": "mf", "password": "pw12345"})


def _setup(db_session, n_ops=2, qty=2):
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
    prod.release_work_order(wo.id)
    db_session.flush()
    return ws, wo, line


def test_ready_page_has_three_sections(client, db_session):
    ws, wo, line = _setup(db_session)
    resp = client.get("/production/station")
    assert resp.status_code == 200
    assert "作业站" in resp.text and "工单" in resp.text and "扫" in resp.text


def test_e2e_first_station_bind_view_pass_reset(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    # 1) 选作业站→选工单→扫载体码 → 进入主界面
    r1 = client.post("/production/station/enter",
                     data={"work_station_id": str(ws[0].id),
                           "work_order_id": str(wo.id), "scan": "PALLET-1"})
    assert r1.status_code == 200 and "工艺路径" in r1.text
    # 2) 验证只绑、不过站
    su = SerialUnitRepository(db_session).get_active_by_carrier("PALLET-1")
    assert su.status == "pending"
    assert OperationRecordRepository(db_session).list_by_serial_unit(su.id) == []
    # 3) 手动 PASS 首工序
    r2 = client.post("/production/station/pass",
                     data={"work_station_id": str(ws[0].id), "scan": "PALLET-1"})
    assert r2.status_code == 200 and "已过" in r2.text
    # 4) PASS 后 SerialUnit 推进 + 有 OperationRecord
    db_session.refresh(su)
    assert su.current_operation_seq == 1 and su.status == "in_process"
    assert len(OperationRecordRepository(db_session).list_by_serial_unit(su.id)) == 1


def test_pass_result_keeps_work_order_context(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=1, qty=2)  # 单工序，首站即完工
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-1"})
    r = client.post("/production/station/pass",
                    data={"work_station_id": str(ws[0].id), "scan": "PALLET-1"})
    assert r.status_code == 200
    # 重置片段带工单上下文：仍有工单号 / work_order_id 供扫下一件
    assert str(wo.id) in r.text or wo.code in r.text
