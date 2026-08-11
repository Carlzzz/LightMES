from datetime import datetime, timedelta
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate,
)
from lightmes.modules.production.planner_service import PlannerService
from lightmes.shared.errors import BusinessRuleError, ConflictError, NotFoundError


def _env(db_session, n_lines=2):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PPP", name="壳", type="finished"))
    lines = [md.create_line(LineCreate(code=f"PLL{i}", name=f"线{i}")) for i in range(n_lines)]
    w = md.create_work_station(WorkStationCreate(
        code="PPW", name="站", line_id=lines[0].id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="PPR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PPR1", name="r", pattern="PP{SEQ:4}"))
    return p, lines, r, rule


def _mk_wo(db_session, line, p, r, rule, code="PPWO"):
    return ProductionService(db_session).create_work_order(WorkOrderCreate(
        code=code, product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))


def test_list_backlog_returns_unscheduled(db_session):
    p, lines, r, rule = _env(db_session)
    # 新建工单：有 line_id 但 planned_start IS NULL → 属于 backlog。
    # （brief 原稿试图把 line_id 置 NULL 模拟"未排程"，但 line_id 是 NOT NULL；
    # service 设计本身就是 planned_start IS NULL OR line_id IS NULL，
    # 因此仅靠 planned_start=None 即可命中 backlog。）
    wo = _mk_wo(db_session, lines[0], p, r, rule)
    db_session.flush()
    backlog = PlannerService(db_session).list_backlog()
    assert wo in backlog


def test_list_backlog_excludes_scheduled(db_session):
    p, lines, r, rule = _env(db_session)
    # brief 原稿 _mk_wo(...) 缺 rule 位置参数，补齐。
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="PPW1")
    wo.planned_start = datetime(2026, 8, 11, 8, 0)
    wo.planned_end = datetime(2026, 8, 11, 16, 0)
    db_session.flush()
    backlog = PlannerService(db_session).list_backlog()
    assert wo not in backlog


def test_detect_conflict_returns_overlapping_wo(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="C1")
    wo1.planned_start = datetime(2026, 8, 11, 8, 0)
    wo1.planned_end = datetime(2026, 8, 11, 16, 0)
    db_session.flush()
    # 同产线 12:00-20:00 与 wo1 重叠
    conflict = PlannerService(db_session).detect_conflict(
        lines[0].id, datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 20, 0))
    assert conflict is not None
    assert conflict.id == wo1.id


def test_detect_conflict_no_overlap_returns_none(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="N1")
    wo1.planned_start = datetime(2026, 8, 11, 8, 0)
    wo1.planned_end = datetime(2026, 8, 11, 16, 0)
    db_session.flush()
    # 17:00 之后无冲突
    conflict = PlannerService(db_session).detect_conflict(
        lines[0].id, datetime(2026, 8, 11, 17, 0), datetime(2026, 8, 11, 20, 0))
    assert conflict is None


def test_schedule_success_logs_no_conflict(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="S1")
    result = PlannerService(db_session).schedule(
        wo.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    assert result.planned_start == datetime(2026, 8, 11, 8, 0)
    assert result.line_id == lines[0].id


def test_schedule_blocks_on_conflict(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="B1")
    PlannerService(db_session).schedule(
        wo1.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    wo2 = _mk_wo(db_session, lines[0], p, r, rule, code="B2")
    with pytest.raises(ConflictError):
        PlannerService(db_session).schedule(
            wo2.id, lines[0].id,
            datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 20, 0),
            user_id=None)


def test_schedule_force_conflict_allows_overlap(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="F1")
    PlannerService(db_session).schedule(
        wo1.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    wo2 = _mk_wo(db_session, lines[0], p, r, rule, code="F2")
    result = PlannerService(db_session).schedule(
        wo2.id, lines[0].id,
        datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 20, 0),
        user_id=None, force=True)
    assert result.planned_start == datetime(2026, 8, 11, 12, 0)


def test_schedule_rejects_end_before_start(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="EB1")
    with pytest.raises(BusinessRuleError):
        PlannerService(db_session).schedule(
            wo.id, lines[0].id,
            datetime(2026, 8, 11, 16, 0), datetime(2026, 8, 11, 8, 0),
            user_id=None)


def test_unschedule_clears_planned_times(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="U1")
    PlannerService(db_session).schedule(
        wo.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    result = PlannerService(db_session).unschedule(wo.id, user_id=None)
    assert result.planned_start is None
    assert result.planned_end is None


def test_unschedule_unknown_raises(db_session):
    with pytest.raises(NotFoundError):
        PlannerService(db_session).unschedule(99999, user_id=None)


def test_list_recent_changes_returns_latest(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="LC1")
    svc = PlannerService(db_session)
    svc.schedule(wo.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    changes = svc.list_recent_changes(limit=10)
    assert len(changes) >= 1
    assert changes[0].action == "schedule"
    assert changes[0].work_order_id == wo.id


def test_undo_change_restores_before_state(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="UN1")
    svc = PlannerService(db_session)
    svc.schedule(wo.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    db_session.flush()
    changes = svc.list_recent_changes(limit=1)
    log_id = changes[0].id
    svc.undo_change(log_id, user_id=None)
    db_session.refresh(wo)
    assert wo.planned_start is None  # before 状态是未排程
    assert wo.planned_end is None


def test_undo_change_blocks_when_before_window_taken(db_session):
    """undo 时若 before 时间窗已被其他 WO 占用 → ConflictError。"""
    from datetime import datetime
    from lightmes.shared.errors import ConflictError
    p, lines, r, rule = _env(db_session)
    # wo1 排到 8-12
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="UB1")
    PlannerService(db_session).schedule(
        wo1.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 12, 0),
        user_id=None)
    log1 = PlannerService(db_session).list_recent_changes(limit=1)[0]
    # wo2 排到 8-12（force）— 占用 8-12
    wo2 = _mk_wo(db_session, lines[0], p, r, rule, code="UB2")
    PlannerService(db_session).schedule(
        wo2.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 12, 0),
        user_id=None, force=True)
    # wo1 移到 13-17（before=8-12）。再排 wo3 到 8-12（force）
    wo3 = _mk_wo(db_session, lines[0], p, r, rule, code="UB3")
    PlannerService(db_session).schedule(
        wo3.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 12, 0),
        user_id=None, force=True)
    PlannerService(db_session).schedule(
        wo1.id, lines[0].id,
        datetime(2026, 8, 11, 13, 0), datetime(2026, 8, 11, 17, 0),
        user_id=None)
    # 现在 undo wo1 的最后一次 schedule：before 是 8-12，已被 wo3 占用 → 冲突
    last_log = PlannerService(db_session).list_recent_changes(limit=1)[0]
    with pytest.raises(ConflictError):
        PlannerService(db_session).undo_change(last_log.id, user_id=None)


def test_undo_already_undone_raises(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="UD1")
    svc = PlannerService(db_session)
    svc.schedule(wo.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    log_id = svc.list_recent_changes(limit=1)[0].id
    svc.undo_change(log_id, user_id=None)
    with pytest.raises(BusinessRuleError):
        svc.undo_change(log_id, user_id=None)  # 重复 undo
