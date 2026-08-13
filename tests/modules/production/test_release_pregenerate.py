import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.repository import SerialUnitRepository


def _wo(db_session, qty=3, with_rule=True):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule_id = None
    if with_rule:
        rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
        rule_id = rule.id
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=qty, sn_rule_id=rule_id))
    return prod, wo


def test_release_pregenerates_pending_units(db_session):
    prod, wo = _wo(db_session, qty=3)
    prod.release_work_order(wo.id)
    repo = SerialUnitRepository(db_session)
    units = repo.list_by_work_order(wo.id)
    assert len(units) == 3
    assert all(u.status == "pending" and u.carrier_code is None
               and u.current_operation_seq == 0 for u in units)
    # SN 连续
    sns = sorted(u.sn for u in units)
    assert sns == ["SN00001", "SN00002", "SN00003"]
    assert repo.count_pending_by_work_order(wo.id) == 3


def test_release_requires_sn_rule(db_session):
    prod, wo = _wo(db_session, qty=3, with_rule=False)
    with pytest.raises(ValueError):
        prod.release_work_order(wo.id)


def test_create_work_order_requires_positive_qty(db_session):
    # qty=0 的工单下达应拒绝
    with pytest.raises(ValueError, match="工单数量须大于 0"):
        _wo(db_session, qty=0)


def test_release_twice_no_duplicate(db_session):
    prod, wo = _wo(db_session, qty=2)
    prod.release_work_order(wo.id)
    with pytest.raises(ValueError):  # 已 released 不可再下达
        prod.release_work_order(wo.id)
    assert SerialUnitRepository(db_session).count_pending_by_work_order(wo.id) == 2
