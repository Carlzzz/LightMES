import pytest
from sqlalchemy import func, select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import OperationRecord
from lightmes.modules.production.repository import SerialUnitRepository, WorkOrderRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import SkillError


def _setup(db_session, required_skill=False, op_level=None, operator_level=None):
    md = MasterDataService(db_session)
    sk = SkillService(db_session)
    user = User(username="skop", password_hash="x", display_name="技工")
    db_session.add(user)
    db_session.flush()
    line = md.create_line(LineCreate(code="SKL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SKW1", name="站1", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SKP", name="件", type="finished"))
    skill = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id)]
    routing = md.create_routing(RoutingCreate(code="SKRT", name="路线", product_id=p.id, operations=ops))
    if required_skill:
        op = md.routings.operations_of(routing.id)[0]
        op.required_skill_id = skill.id
        op.required_level = op_level
        db_session.flush()
    if operator_level is not None:
        sk.set_operator_skill(user.id, skill.id, operator_level)
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code="SKSR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SKWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5,
        sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, ws, user


def _blocked_pass_wo_counts(db, wo_code="SKWO"):
    """被拒过站后，工单下 SerialUnit / OperationRecord 的数量。

    OperationRecord 是真正的完工记录（在 5b 校验之后的 step6 才写入），
    硬拦截保证它必须为 0 —— 这是本文件要固化的核心安全保证。

    SerialUnit 在 step3 生成 SN 时已 flush（见 operation_pass_service
    SerialUnitRepository.add → db.flush()），早于 5b 校验；测试会话没有
    get_db 的请求级 rollback，所以该 pending 单位仍可见（count == 1）。
    生产环境由 get_db 的 except → rollback 丢弃，不落库。
    """
    wo = WorkOrderRepository(db).get_by_code(wo_code)
    serial_unit_count = len(SerialUnitRepository(db).list_by_work_order(wo.id))
    op_record_count = db.execute(
        select(func.count()).select_from(OperationRecord)
        .where(OperationRecord.work_order_id == wo.id)
    ).scalar_one()
    return wo.id, serial_unit_count, op_record_count


def test_pass_no_skill_requirement_ok(db_session):
    db, ws, user = _setup(db_session, required_skill=False)
    r = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    assert r.sn is not None  # 无技能要求 → 放行


def test_pass_sufficient_skill_ok(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=2, operator_level=3)
    r = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    assert r.sn is not None  # 3 >= 2 → 放行


def test_pass_insufficient_skill_blocked(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=3, operator_level=1)
    with pytest.raises(SkillError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    _, su_count, rec_count = _blocked_pass_wo_counts(db)
    assert rec_count == 0  # 硬拦截：未写入任何工序记录
    # step3 生成 SN 时已 flush，测试会话无 get_db 回滚 → pending 单位仍可见(count=1)；
    # 生产由请求级 rollback 丢弃。真正的硬拦截保证是上一条：无任何工序记录。
    assert su_count == 1


def test_pass_no_operator_skill_record_blocked(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=2, operator_level=None)
    with pytest.raises(SkillError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    _, su_count, rec_count = _blocked_pass_wo_counts(db)
    assert rec_count == 0  # 硬拦截：未写入任何工序记录
    # step3 生成 SN 时已 flush，测试会话无 get_db 回滚 → pending 单位仍可见(count=1)；
    # 生产由请求级 rollback 丢弃。真正的硬拦截保证是上一条：无任何工序记录。
    assert su_count == 1


def test_pass_no_operator_id_with_requirement_blocked(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=2, operator_level=3)
    with pytest.raises(SkillError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="SKWO", operator_id=None))
    _, su_count, rec_count = _blocked_pass_wo_counts(db)
    assert rec_count == 0  # 硬拦截：未写入任何工序记录
    # step3 生成 SN 时已 flush，测试会话无 get_db 回滚 → pending 单位仍可见(count=1)；
    # 生产由请求级 rollback 丢弃。真正的硬拦截保证是上一条：无任何工序记录。
    assert su_count == 1
