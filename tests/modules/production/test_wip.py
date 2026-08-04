from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.modules.production.wip_service import WipService


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="WS1", name="上料"))
    s2 = md.create_station(StationCreate(code="WS2", name="装配"))
    r = md.create_routing(RoutingCreate(code="WR", name="路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="WRL", name="r", pattern="W{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WWO", product_id=p.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return s1, s2, wo


def test_wip_by_work_order_lists_in_process(db_session):
    s1, s2, wo = _line(db_session)
    pass_svc = StationPassService(db_session)
    # 两个 SN 过首站，停在 s1 之后（current_step_seq=1）
    pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    wip = WipService(db_session).wip_by_work_order(wo.id)
    assert len(wip) == 2
    assert all(w.status == "in_process" for w in wip)
    assert all(w.current_step_seq == 1 for w in wip)


def test_wip_by_station(db_session):
    s1, s2, wo = _line(db_session)
    pass_svc = StationPassService(db_session)
    pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    wip = WipService(db_session).wip_by_station(s1.id)
    assert len(wip) == 1
    assert wip[0].current_station_id == s1.id


def test_wip_page_renders(db_session):
    import pytest as _pytest
    from fastapi.testclient import TestClient
    from lightmes.main import app
    from lightmes.database import get_db
    s1, s2, wo = _line(db_session)
    StationPassService(db_session).pass_station(
        StationPassInput(station_id=s1.id, work_order_code="WWO"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        resp = client.get(f"/production/wip?work_order_id={wo.id}")
        assert resp.status_code == 200
        assert "WIP 看板" in resp.text
        assert "W001" in resp.text
    finally:
        app.dependency_overrides.clear()


def test_finished_sn_excluded_from_wip(db_session):
    s1, s2, wo = _line(db_session)
    pass_svc = StationPassService(db_session)
    # 第一单过完两站（seq1@s1 → seq2@s2），末站完工
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    assert res.is_finished is False
    finished_sn = res.sn
    res2 = pass_svc.pass_station(StationPassInput(station_id=s2.id, sn=finished_sn))
    assert res2.is_finished is True
    # 第二单只过首站，仍在制
    in_process_sn = pass_svc.pass_station(
        StationPassInput(station_id=s1.id, work_order_code="WWO")).sn
    wip = WipService(db_session).wip_by_work_order(wo.id)
    wip_sns = [w.sn for w in wip]
    assert finished_sn not in wip_sns
    assert in_process_sn in wip_sns
    # 完工于 s2 但 status=finished，站级在制列表也应排除
    station_wip_sns = [w.sn for w in WipService(db_session).wip_by_station(s2.id)]
    assert finished_sn not in station_wip_sns


def test_wip_page_empty_without_work_order(db_session):
    from fastapi.testclient import TestClient
    from lightmes.main import app
    from lightmes.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        resp = client.get("/production/wip")
        assert resp.status_code == 200
        assert "WIP 看板" in resp.text
        # 无 work_order_id → 空列表，tbody 中无数据行，仅表头一行 <tr>
        assert resp.text.count("<tr>") == 1
    finally:
        app.dependency_overrides.clear()
