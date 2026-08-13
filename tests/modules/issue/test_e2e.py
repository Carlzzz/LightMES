"""Issue/Andon 完整 happy-path 端到端。

流程：
1. operator 在 station 创建 blocking issue (ANDON)
2. station pass 被阻断
3. supervisor acknowledge → resolve
4. operator 再次 pass 通过
5. supervisor close
"""
import pytest
from lightmes.modules.issue.models import Issue, IssueType
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.schemas import OperationPassInput
from lightmes.modules.issue.service import IssueService
from lightmes.shared.errors import BusinessRuleError


def test_e2e_blocking_lifecycle(
        db_session, sample_user, full_station_setup):
    su = full_station_setup.serial_unit
    ws_id = full_station_setup.work_station_id

    # 1. operator 建 blocking issue
    t = IssueType(code="E2E_BLOCK", name="b", severity="critical", is_blocking=True)
    db_session.add(t); db_session.flush()
    svc = IssueService(db_session)
    issue = svc.create_issue(
        issue_type_id=t.id, title="设备卡死",
        source="station_andon", serial_unit_id=su.id,
        work_station_id=ws_id, reported_by_id=sample_user.id)
    db_session.flush()
    assert svc.is_blocking(issue) is True

    # 2. pass 被阻断
    op_svc = OperationPassService(db_session)
    with pytest.raises(BusinessRuleError) as exc:
        op_svc.pass_operation(OperationPassInput(
            sn=su.sn, work_station_id=ws_id,
            operator_id=sample_user.id, components=[], params=[]))
    assert "Issue #" in str(exc.value)

    # 3. supervisor acknowledge + resolve
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="主轴卡死", containment_action="已修",
                disposition="use_as_is")
    db_session.flush()
    assert svc.is_blocking(issue) is False  # resolved 不阻断

    # 4. 再 pass 通过（不再 raise IssueBlockError）
    try:
        op_svc.pass_operation(OperationPassInput(
            sn=su.sn, work_station_id=ws_id,
            operator_id=sample_user.id, components=[], params=[]))
    except BusinessRuleError as e:
        # 其他业务错误 OK，只要不是 Issue 阻断
        assert "Issue #" not in str(e)

    # 5. close（无 CAPA，直接通过）
    svc.close(issue.id, sample_user.id)
    db_session.flush()
    db_session.refresh(issue)
    assert issue.status == "closed"


def test_e2e_capa_blocks_close(
        db_session, sample_user, full_station_setup):
    """有未验证 CAPA 时 close 失败。"""
    su = full_station_setup.serial_unit  # noqa: F841（占位保持 setup）
    t = IssueType(code="E2E_CAPA", name="b", severity="minor")
    db_session.add(t); db_session.flush()
    svc = IssueService(db_session)
    issue = svc.create_issue(
        issue_type_id=t.id, title="x",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    svc.add_action(issue.id, type="corrective", title="a")  # status=open

    with pytest.raises(BusinessRuleError):
        svc.close(issue.id, sample_user.id)
