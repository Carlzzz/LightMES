import uuid

import pytest
from lightmes.modules.issue.models import Issue, IssueAction, IssueType
from lightmes.modules.issue.service import IssueService
from lightmes.modules.production.models import SerialUnit
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


def _build_serial_unit(db_session) -> SerialUnit:
    """构造 SerialUnit 满足 FK 链（product→line→station→routing→work_order）。
    共享 dev 库不会存在 id=42 的 SN，所以必须建真 SN。
    """
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate

    tag = uuid.uuid4().hex[:8]
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code=f"P{tag}", name="壳", type="finished"))
    line = md.create_line(LineCreate(code=f"L{tag}", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code=f"W{tag}", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code=f"R{tag}", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code=f"OP{tag}", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code=f"WO{tag}", product_id=p.id, routing_id=r.id,
                        line_id=line.id, qty=5))
    su = SerialUnit(sn=f"SN{tag}", work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    return su


@pytest.fixture
def type_minor(db_session):
    it = IssueType(code="T_minor", name="minor", severity="minor", is_blocking=False)
    db_session.add(it); db_session.flush()
    return it


@pytest.fixture
def type_block(db_session):
    it = IssueType(code="T_block", name="block", severity="critical", is_blocking=True)
    db_session.add(it); db_session.flush()
    return it


@pytest.fixture
def serial_unit(db_session):
    return _build_serial_unit(db_session)


@pytest.fixture
def svc(db_session):
    return IssueService(db_session)


def test_create_issue_snapshots_severity_from_type(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    assert issue.severity == "minor"
    assert issue.status == "open"
    assert issue.source == "manual"


def test_create_issue_empty_title_rejected(svc, type_minor, sample_user):
    with pytest.raises(ValidationError):
        svc.create_issue(
            issue_type_id=type_minor.id, title="  ",
            reported_by_id=sample_user.id)


def test_acknowledge_requires_open(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    assert issue.status == "acknowledged"
    # 再 ack 应失败
    with pytest.raises(BusinessRuleError):
        svc.acknowledge(issue.id, sample_user.id)


def test_resolve_requires_all_fields(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    with pytest.raises(ValidationError):
        svc.resolve(issue.id, sample_user.id,
                    root_cause="", containment_action="x",
                    disposition="rework")
    with pytest.raises(ValidationError):
        svc.resolve(issue.id, sample_user.id,
                    root_cause="x", containment_action="x",
                    disposition="bogus")


def test_close_blocked_when_unverified_capa(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    svc.add_action(issue.id, type="corrective", title="a")  # 默认 status=open
    with pytest.raises(BusinessRuleError):
        svc.close(issue.id, sample_user.id)


def test_close_passes_when_all_capa_verified(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    a = svc.add_action(issue.id, type="corrective", title="a")
    svc.start_action(a.id, sample_user.id)
    svc.complete_action(a.id, sample_user.id)
    svc.verify_action(a.id, sample_user.id)
    svc.close(issue.id, sample_user.id)
    assert issue.status == "closed"


def test_reopen_only_from_closed(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    with pytest.raises(BusinessRuleError):
        svc.reopen(issue.id, sample_user.id, reason="x")


def test_reopen_requires_reason(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    svc.close(issue.id, sample_user.id)
    with pytest.raises(ValidationError):
        svc.reopen(issue.id, sample_user.id, reason="")


def test_is_blocking_combines_type_and_status(svc, type_block, sample_user, serial_unit):
    issue = svc.create_issue(
        issue_type_id=type_block.id, title="t",
        serial_unit_id=serial_unit.id, reported_by_id=sample_user.id)
    assert svc.is_blocking(issue) is True
    svc.acknowledge(issue.id, sample_user.id)
    assert svc.is_blocking(issue) is True  # acknowledged 仍阻断
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    assert svc.is_blocking(issue) is False  # resolved 不阻断


def test_check_block_for_sn_returns_latest(svc, type_block, sample_user, serial_unit):
    """多条 blocking 返回 id 最大的（最新）。"""
    older = svc.create_issue(
        issue_type_id=type_block.id, title="old",
        serial_unit_id=serial_unit.id, reported_by_id=sample_user.id)
    newer = svc.create_issue(
        issue_type_id=type_block.id, title="new",
        serial_unit_id=serial_unit.id, reported_by_id=sample_user.id)
    found = svc.check_block_for_sn(serial_unit.id)
    assert found.id == newer.id


def test_capa_lifecycle_full(svc, type_minor, sample_user):
    """open → in_progress → done → verified 全链。"""
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    a = svc.add_action(issue.id, type="corrective", title="a")
    assert a.status == "open"

    svc.start_action(a.id, sample_user.id)
    assert a.status == "in_progress"

    svc.complete_action(a.id, sample_user.id)
    assert a.status == "done"
    assert a.completed_at is not None

    svc.verify_action(a.id, sample_user.id)
    assert a.status == "verified"
    assert a.verified_at is not None


def test_capa_verify_requires_done(svc, type_minor, sample_user):
    """未 done 直接 verify 失败。"""
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    a = svc.add_action(issue.id, type="corrective", title="a")
    with pytest.raises(BusinessRuleError):
        svc.verify_action(a.id, sample_user.id)
