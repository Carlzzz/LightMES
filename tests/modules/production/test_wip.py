from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.wip_service import WipService


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="wipop", password="pw12345", display_name="Wip"))
    db_session.flush()
    assert client.post(
        "/login",
        data={"username": "wipop", "password": "pw12345"},
    ).status_code == 204


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="WPL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(
        code="WS1W", name="上料站", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="WS2W", name="装配站", line_id=line.id, seq=2))
    r = md.create_routing(RoutingCreate(code="WR", name="路线", product_id=p.id,
        operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id, allowed_work_station_ids=[w1.id]),
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id, allowed_work_station_ids=[w2.id]),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="WRL", name="r", pattern="W{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return w1, w2, wo


def test_wip_by_work_order_lists_in_process(db_session):
    w1, w2, wo = _line(db_session)
    pass_svc = OperationPassService(db_session)
    # 两个 SN 过首站，停在 w1 之后（current_operation_seq=1）
    pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="WWO"))
    pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="WWO"))
    wip = WipService(db_session).wip_by_work_order(wo.id)
    assert len(wip) == 2
    assert all(w.status == "in_process" for w in wip)
    assert all(w.current_operation_seq == 1 for w in wip)


def test_wip_page_renders(db_session):
    import pytest as _pytest
    from fastapi.testclient import TestClient
    from lightmes.main import app
    from lightmes.database import get_db
    w1, w2, wo = _line(db_session)
    OperationPassService(db_session).pass_operation(
        OperationPassInput(work_station_id=w1.id, work_order_code="WWO"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        _login(client, db_session)
        resp = client.get(f"/production/wip?work_order={wo.code}")
        assert resp.status_code == 200
        assert "WIP 看板" in resp.text
        assert "W001" in resp.text
    finally:
        app.dependency_overrides.clear()


def test_finished_sn_excluded_from_wip(db_session):
    w1, w2, wo = _line(db_session)
    pass_svc = OperationPassService(db_session)
    # 第一单过完两站（seq1@w1 → seq2@w2），末站完工
    res = pass_svc.pass_operation(OperationPassInput(work_station_id=w1.id, work_order_code="WWO"))
    assert res.is_finished is False
    finished_sn = res.sn
    res2 = pass_svc.pass_operation(OperationPassInput(work_station_id=w2.id, sn=finished_sn))
    assert res2.is_finished is True
    # 第二单只过首站，仍在制
    in_process_sn = pass_svc.pass_operation(
        OperationPassInput(work_station_id=w1.id, work_order_code="WWO")).sn
    wip = WipService(db_session).wip_by_work_order(wo.id)
    wip_sns = [w.sn for w in wip]
    assert finished_sn not in wip_sns
    assert in_process_sn in wip_sns


def test_wip_page_empty_without_work_order(db_session):
    from fastapi.testclient import TestClient
    from lightmes.main import app
    from lightmes.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        _login(client, db_session)
        resp = client.get("/production/wip")
        assert resp.status_code == 200
        assert "WIP 看板" in resp.text
        # 无 work_order_id → 无数据表格，显示空状态提示，且无任何数据行
        assert resp.text.count("<tr>") == 0
        assert "暂无在制品" in resp.text
    finally:
        app.dependency_overrides.clear()
