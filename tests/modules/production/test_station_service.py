import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.station_service import StationService
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError


def _setup(db_session, required_skill=False, op_level=None, operator_level=None,
           with_bom=False):
    md = MasterDataService(db_session)
    sk = SkillService(db_session)
    user = User(username="stop", password_hash="x", display_name="工人张")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="P", name="成品", type="finished"))
    comp = md.create_product(ProductCreate(code="C1", name="组件1", type="component"))
    skill = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    ops = [
        OperationCreate(seq=10, code="OP10", name="工序10", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
        OperationCreate(seq=20, code="OP20", name="工序20", default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    if required_skill:
        op0 = md.routings.operations_of(routing.id)[0]
        op0.required_skill_id = skill.id
        op0.required_level = op_level
        db_session.flush()
    if operator_level is not None:
        sk.set_operator_skill(user.id, skill.id, operator_level)
    if with_bom:
        bom = md.create_bom(BomCreate(product_id=p.id, items=[
            BomItemCreate(component_product_id=comp.id, qty=2)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, ws1, ws2, user, comp


def test_load_first_item_by_work_order(db_session):
    db, ws1, ws2, user, comp = _setup(db_session)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.work_order_code == "WO"
    assert view.product_name == "成品"
    assert view.operator_name == "工人张"
    # 首件 current_seq=0 → 第一道工序为 current，其余 future
    assert [o.status for o in view.operations] == ["current", "future"]
    assert view.current_op.seq == 10
    assert view.is_finished is False


def test_load_no_skill_requirement_ok(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, required_skill=False)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.required_level is None
    assert view.skill_ok is True
    assert view.operator_skill_level is None


def test_load_skill_insufficient_flags_not_ok(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, required_skill=True, op_level=3, operator_level=1)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.required_level == 3
    assert view.operator_skill_level == 1
    assert view.skill_ok is False  # 1 < 3


def test_load_skill_sufficient_ok(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, required_skill=True, op_level=2, operator_level=3)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.skill_ok is True  # 3 >= 2


def test_load_off_station_raises(db_session):
    # 当前工序在 ws1，但用 ws2 加载 → 直接抛 BusinessRuleError（不进入富界面）
    import pytest as _pytest
    from lightmes.shared.errors import BusinessRuleError
    db, ws1, ws2, user, comp = _setup(db_session)
    with _pytest.raises(BusinessRuleError):
        StationService(db).load("WO", ws2.id, user.id)


def test_load_components_from_active_bom(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, with_bom=True)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert len(view.components) == 1
    assert view.components[0].component_code == "C1"
    assert view.components[0].qty == 2


def test_load_unknown_scan_raises_not_found(db_session):
    db, ws1, ws2, user, comp = _setup(db_session)
    with pytest.raises(NotFoundError):
        StationService(db).load("NOPE", ws1.id, user.id)


def test_load_by_active_carrier_code(db_session):
    # 扫"载体码"加载 → 命中该 SN 单元的 StationView
    # 注意：首站在 ws1 过 OP10 后，单元当前工序变 OP20(ws2)，载体码加载须在 ws2
    db, ws1, ws2, user, comp = _setup(db_session)
    r = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="WO"))
    su = SerialUnitRepository(db).get_by_sn(r.sn)
    su.carrier_code = "CARRIER-1"
    db.flush()
    view = StationService(db).load("CARRIER-1", ws2.id, user.id)
    assert view.sn == r.sn
    assert view.work_order_code == "WO"


def test_load_station_operations_subset(db_session):
    """Layer 2 仅含本站 allowed 工序子集。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.station_service import StationService
    from lightmes.modules.auth.models import User

    md = MasterDataService(db_session)
    user = User(username="ssop", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="SSL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="SS1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="SS2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="SSP", name="件", type="finished"))
    # op1 仅 ws1，op2 仅 ws2，op3 ws1+ws2
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
        OperationCreate(seq=3, code="OP3", name="工序3", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SSRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SSSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="SSWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 在 ws1 加载（首件 pending）
    view = StationService(db_session).load("SSWO", ws1.id, user.id)
    # Layer 2 应仅含 op1, op3（ws1 allowed）
    station_op_seqs = [o.seq for o in view.station_operations]
    assert station_op_seqs == [1, 3]


def test_load_skipped_status_after_skip(db_session):
    """跳站后 Layer 1 显示 skipped 状态。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.station_service import StationService
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.auth.models import User

    md = MasterDataService(db_session)
    user = User(username="skip2", password_hash="x", display_name="主管")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="SKL2", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SKW2", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SKP2", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=3, code="OP3", name="工序3", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SKRT2", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SKSR2", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="SKWO2", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # pass op1, skip op2
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO2", operator_id=user.id))
    su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]
    OperationPassService(db_session).skip_operation(OperationSkipInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="跳过 op2"))
    # 加载
    view = StationService(db_session).load(su.sn, ws.id, user.id)
    status_by_seq = {o.seq: o.status for o in view.operations}
    assert status_by_seq[1] == "done"
    assert status_by_seq[2] == "skipped"
    assert status_by_seq[3] == "current"
