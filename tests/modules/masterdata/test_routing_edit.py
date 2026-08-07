import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.repository import (
    OperationWorkStationRepository, RoutingRepository,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.repository import WorkOrderRepository


def _full_setup(db_session, with_work_order=False):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
        OperationCreate(seq=20, code="OP20", name="工序20",
                        default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
    ]))
    if with_work_order:
        prod = ProductionService(db_session)
        rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
        prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id,
            line_id=line.id, qty=5, sn_rule_id=rule.id))
    db_session.flush()
    return md, p, line, (ws1, ws2), routing


def test_update_routing_head(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    updated = md.update_routing_head(routing.id, "新名称")
    assert updated.name == "新名称"


def test_update_routing_head_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    with pytest.raises(ValueError, match="工单"):
        md.update_routing_head(routing.id, "新名称")


def test_set_routing_status_active_conflict(db_session):
    md, p, line, wss, routing = _full_setup(db_session)  # routing active
    other = md.create_routing(RoutingCreate(code="RT2", name="路线2", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OPX", name="工序X",
                        default_work_station_id=wss[0].id, allowed_work_station_ids=[wss[0].id])]))
    # other 默认 inactive（同产品已有 active routing）
    assert other.status == "inactive"
    with pytest.raises(ValueError, match="active 路线"):
        md.set_routing_status(other.id, "active")  # 冲突
    # 先把 routing inactive，再 active other → 通过
    md.set_routing_status(routing.id, "inactive")
    md.set_routing_status(other.id, "active")
    db_session.refresh(other)
    assert other.status == "active"


def test_set_routing_status_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    with pytest.raises(ValueError, match="工单"):
        md.set_routing_status(routing.id, "inactive")


def test_update_operation_changes_fields_and_allowed(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    ops = md.routings.operations_of(routing.id)
    op0 = ops[0]
    updated = md.update_operation(
        op0.id, seq=15, code="OP15", name="新工序名",
        default_work_station_id=wss[1].id,
        allowed_work_station_ids=[wss[0].id, wss[1].id],
        required_skill_id=None, required_level=None, is_mandatory=True)
    assert updated.seq == 15 and updated.name == "新工序名"
    allowed = OperationWorkStationRepository(db_session).list_by_operation(updated.id)
    assert {a.work_station_id for a in allowed} == {wss[0].id, wss[1].id}


def test_update_operation_rejects_default_not_in_allowed(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    op0 = md.routings.operations_of(routing.id)[0]
    with pytest.raises(ValueError, match="默认作业站"):
        md.update_operation(
            op0.id, seq=10, code="OP10", name="工序10",
            default_work_station_id=wss[0].id,
            allowed_work_station_ids=[wss[1].id],  # default wss[0] 不在
            required_skill_id=None, required_level=None, is_mandatory=True)


def test_update_operation_rejects_seq_conflict(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    ops = md.routings.operations_of(routing.id)
    op0 = ops[0]  # seq=10
    with pytest.raises(ValueError, match="seq"):  # 改成 op1 的 seq=20
        md.update_operation(
            op0.id, seq=20, code="OP10", name="工序10",
            default_work_station_id=wss[0].id, allowed_work_station_ids=[wss[0].id],
            required_skill_id=None, required_level=None, is_mandatory=True)


def test_update_operation_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    op0 = md.routings.operations_of(routing.id)[0]
    with pytest.raises(ValueError, match="工单"):
        md.update_operation(
            op0.id, seq=15, code="OP15", name="x",
            default_work_station_id=wss[0].id, allowed_work_station_ids=[wss[0].id],
            required_skill_id=None, required_level=None, is_mandatory=True)


def test_add_operation(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    new_op = md.add_operation(
        routing.id, seq=30, code="OP30", name="工序30",
        default_work_station_id=wss[1].id, allowed_work_station_ids=[wss[1].id],
        required_skill_id=None, required_level=None, is_mandatory=True)
    assert new_op.id is not None and new_op.seq == 30


def test_delete_operation(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    op0 = md.routings.operations_of(routing.id)[0]
    md.delete_operation(op0.id)
    db_session.flush()
    remaining = md.routings.operations_of(routing.id)
    assert len(remaining) == 1 and remaining[0].seq == 20
    # 关联表跟随清
    assert OperationWorkStationRepository(db_session).list_by_operation(op0.id) == []


def test_delete_operation_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    op0 = md.routings.operations_of(routing.id)[0]
    with pytest.raises(ValueError, match="工单"):
        md.delete_operation(op0.id)


def test_delete_routing_cascades(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    op_ids = [o.id for o in md.routings.operations_of(routing.id)]
    md.delete_routing(routing.id)
    db_session.flush()
    assert RoutingRepository(db_session).get(routing.id) is None
    # operations 全部级联清
    for oid in op_ids:
        assert OperationWorkStationRepository(db_session).list_by_operation(oid) == []


def test_delete_routing_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    with pytest.raises(ValueError, match="工单"):
        md.delete_routing(routing.id)
