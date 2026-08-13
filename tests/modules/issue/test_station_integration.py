import pytest
from lightmes.modules.issue.models import Issue, IssueType
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.schemas import OperationPassInput
from lightmes.shared.errors import BusinessRuleError


@pytest.fixture
def blocking_type(db_session):
    t = IssueType(code="T_block_int", name="b", severity="critical", is_blocking=True)
    db_session.add(t); db_session.flush()
    return t


def test_pass_blocked_when_sn_has_open_blocking_issue(
        db_session, blocking_type, sample_user, full_station_setup):
    """已 setup 一个 SN + 工单 + 工艺 + 工位；创建 blocking issue 后 pass 应失败。"""
    su = full_station_setup.serial_unit
    db_session.add(Issue(
        issue_type_id=blocking_type.id, title="bad", severity="critical",
        source="manual", serial_unit_id=su.id, status="open",
        reported_by_id=sample_user.id))
    db_session.flush()

    svc = OperationPassService(db_session)
    data = OperationPassInput(
        sn=su.sn, work_station_id=full_station_setup.work_station_id,
        operator_id=sample_user.id, components=[], params=[])
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(data)
    assert "Issue #" in str(exc.value)


def test_pass_allowed_after_blocking_resolved(
        db_session, blocking_type, sample_user, full_station_setup):
    """resolved 状态的 blocking issue 不阻断。"""
    su = full_station_setup.serial_unit
    db_session.add(Issue(
        issue_type_id=blocking_type.id, title="bad", severity="critical",
        source="manual", serial_unit_id=su.id, status="resolved",
        reported_by_id=sample_user.id))
    db_session.flush()
    svc = OperationPassService(db_session)
    # 不应 raise IssueBlockError —— 可能因其他业务规则失败，但不是阻断
    try:
        svc.pass_operation(OperationPassInput(
            sn=su.sn, work_station_id=full_station_setup.work_station_id,
            operator_id=sample_user.id, components=[], params=[]))
    except BusinessRuleError as e:
        assert "Issue #" not in str(e)
