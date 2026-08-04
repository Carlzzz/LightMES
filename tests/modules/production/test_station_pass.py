import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, LineCreate, WorkStationCreate,
    RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.models import RoutingStep
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.shared.errors import NotFoundError, BusinessRuleError


def _setup(db_session, qty=10):
    """建产品 + 两工位两工序路线 + SN规则 + 已下达工单。返回 (p, s1, s2, wo)。"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="FP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="FPL", name="线"))
    s1 = md.create_station(StationCreate(code="ST1", name="上料"))
    s2 = md.create_station(StationCreate(code="ST2", name="装配"))
    w1 = md.create_work_station(WorkStationCreate(
        code="ST1W", name="上料站", line_id=line.id, seq=1))
    w2 = md.create_work_station(WorkStationCreate(
        code="ST2W", name="装配站", line_id=line.id, seq=2))
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[
            OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
            OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
        ]))
    # 旧生产层 StationPassService 仍读 routing_steps —— 补建
    db_session.add_all([
        RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"),
        RoutingStep(routing_id=r.id, seq=2, station_id=s2.id, name="装配"),
    ])
    db_session.flush()
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RL", name="r", pattern="SN{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WO1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return p, s1, s2, wo


def test_first_pass_generates_sn_and_advances(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    assert res.sn == "SN0001"
    assert res.passed_step.seq == 1
    assert res.next_step.seq == 2
    assert res.is_finished is False
    assert res.work_order_status == "in_process"  # 首过站翻转


def test_second_pass_by_sn_finishes(db_session):
    p, s1, s2, wo = _setup(db_session, qty=1)
    svc = StationPassService(db_session)
    r1 = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    r2 = svc.pass_station(StationPassInput(station_id=s2.id, sn=r1.sn))
    assert r2.passed_step.seq == 2
    assert r2.next_step is None
    assert r2.is_finished is True
    assert r2.work_order_status == "completed"  # qty=1 完工即 completed


def test_skip_station_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    # 首件却扫到第二个工位 → 防跳站
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s2.id, work_order_code="WO1"))


def test_duplicate_pass_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    r1 = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    # 同一 SN 再扫工位1（已过）→ 期望下一工序是 s2，扫 s1 触发防跳站/防重复
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s1.id, sn=r1.sn))


def test_unknown_work_order_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    with pytest.raises(NotFoundError):
        svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="NOPE"))


def test_unknown_sn_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    with pytest.raises(NotFoundError):
        svc.pass_station(StationPassInput(station_id=s2.id, sn="NOSUCH"))


def test_pass_on_non_released_work_order_rejected(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="FP2", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="FP2L", name="线"))
    s1 = md.create_station(StationCreate(code="STA", name="上料"))
    w1 = md.create_work_station(WorkStationCreate(
        code="STAW", name="上料站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="RT2", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id)]))
    db_session.add(RoutingStep(routing_id=r.id, seq=1, station_id=s1.id, name="上料"))
    db_session.flush()
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RL2", name="r", pattern="A{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WOC", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=5, sn_rule_id=rule.id))
    # 未 release，仍是 created
    svc = StationPassService(db_session)
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WOC"))


def test_finished_sn_cannot_pass_again(db_session):
    p, s1, s2, wo = _setup(db_session, qty=1)
    svc = StationPassService(db_session)
    r1 = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    svc.pass_station(StationPassInput(station_id=s2.id, sn=r1.sn))  # finished
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s2.id, sn=r1.sn))
