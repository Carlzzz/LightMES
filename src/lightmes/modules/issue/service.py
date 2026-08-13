from datetime import datetime
from sqlalchemy.orm import Session

from lightmes.modules.issue.models import Issue, IssueAction, IssueType
from lightmes.modules.issue.repository import (
    IssueActionRepository, IssueRepository, IssueTypeRepository,
)
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


SEVERITY_MAP = {"critical": "critical", "major": "major", "minor": "minor"}


class IssueService:
    def __init__(self, db: Session):
        self.db = db
        self.types = IssueTypeRepository(db)
        self.issues = IssueRepository(db)
        self.actions = IssueActionRepository(db)

    # ---------- 查询辅助 ----------

    @staticmethod
    def is_blocking(issue: Issue) -> bool:
        return (
            issue.issue_type.is_blocking
            and issue.status in ("open", "acknowledged")
        )

    def check_block_for_sn(self, serial_unit_id: int) -> Issue | None:
        """SN 级阻断检查：返回最新阻断 Issue，无则 None。"""
        return self.issues.get_blocking_for_sn(serial_unit_id)

    def _get_or_404(self, issue_id: int) -> Issue:
        issue = self.issues.get(issue_id)
        if issue is None:
            raise NotFoundError(f"Issue 不存在: {issue_id}")
        return issue

    # ---------- 状态机 ----------

    def create_issue(
        self,
        *,
        issue_type_id: int,
        title: str,
        reported_by_id: int,
        description: str | None = None,
        source: str = "manual",
        serial_unit_id: int | None = None,
        work_order_id: int | None = None,
        work_station_id: int | None = None,
        operation_id: int | None = None,
        defect_id: int | None = None,
    ) -> Issue:
        """创建 Issue。severity 从 type snapshot；is_blocking 跟 type 走。"""
        if not title.strip():
            raise ValidationError("title 不可为空")
        it = self.types.get(issue_type_id)
        if it is None or not it.is_active:
            raise NotFoundError(f"IssueType 不存在或已停用: {issue_type_id}")
        issue = Issue(
            issue_type_id=issue_type_id,
            title=title.strip(),
            description=description,
            status="open",
            severity=it.severity,
            source=source,
            serial_unit_id=serial_unit_id,
            work_order_id=work_order_id,
            work_station_id=work_station_id,
            operation_id=operation_id,
            defect_id=defect_id,
            reported_by_id=reported_by_id,
        )
        return self.issues.add(issue)

    def acknowledge(self, issue_id: int, user_id: int) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "open":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 acknowledge")
        issue.status = "acknowledged"
        issue.acknowledged_by_id = user_id
        issue.acknowledged_at = datetime.now()
        return issue

    def resolve(
        self,
        issue_id: int,
        user_id: int,
        *,
        root_cause: str,
        containment_action: str,
        disposition: str,
        resolution_notes: str | None = None,
    ) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "acknowledged":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 resolve")
        if not root_cause.strip() or not containment_action.strip():
            raise ValidationError("root_cause 与 containment_action 不可为空")
        if disposition not in ("use_as_is", "rework", "scrap", "hold"):
            raise ValidationError(f"非法 disposition: {disposition}")
        issue.status = "resolved"
        issue.root_cause = root_cause
        issue.containment_action = containment_action
        issue.disposition = disposition
        issue.resolution_notes = resolution_notes
        issue.resolved_by_id = user_id
        issue.resolved_at = datetime.now()
        return issue

    def close(self, issue_id: int, user_id: int) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "resolved":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 close")
        # CAPA 验证闸
        unverified = self.actions.count_unverified(issue_id)
        if unverified > 0:
            raise BusinessRuleError(
                f"还有 {unverified} 条 CAPA 未 verified，不可 close")
        issue.status = "closed"
        issue.closed_by_id = user_id
        issue.closed_at = datetime.now()
        return issue

    def reopen(self, issue_id: int, user_id: int, *, reason: str) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "closed":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 reopen")
        if not reason.strip():
            raise ValidationError("reopen 须提供 reason")
        issue.status = "open"
        issue.reopen_reason = reason
        # 清除 resolved/closed 时间戳（保留 acknowledged/resolved 字段不动，作为历史）
        issue.closed_by_id = None
        issue.closed_at = None
        return issue

    # ---------- CAPA ----------

    def add_action(
        self,
        issue_id: int,
        *,
        type: str,
        title: str,
        description: str | None = None,
        assigned_to_id: int | None = None,
        due_date=None,
    ) -> IssueAction:
        issue = self._get_or_404(issue_id)
        if issue.status == "closed":
            raise BusinessRuleError("closed 的 Issue 不可加 CAPA（先 reopen）")
        if type not in ("corrective", "preventive", "containment"):
            raise ValidationError(f"非法 CAPA type: {type}")
        if not title.strip():
            raise ValidationError("title 不可为空")
        action = IssueAction(
            issue_id=issue_id,
            type=type,
            title=title.strip(),
            description=description,
            assigned_to_id=assigned_to_id,
            due_date=due_date,
            status="open",
        )
        return self.actions.add(action)

    def _transition_action(
        self, action_id: int, from_statuses: list[str], to_status: str,
        user_id: int, *, role_check: bool = False,
    ) -> IssueAction:
        action = self.actions.get(action_id)
        if action is None:
            raise NotFoundError(f"IssueAction 不存在: {action_id}")
        if action.status not in from_statuses:
            raise BusinessRuleError(
                f"CAPA 当前状态 {action.status} 不可转 {to_status}")
        action.status = to_status
        if to_status == "in_progress":
            pass  # 无时间戳
        elif to_status == "done":
            action.completed_at = datetime.now()
            action.completed_by_id = user_id
        elif to_status == "verified":
            action.verified_at = datetime.now()
            action.verified_by_id = user_id
        # flush 让后续 SELECT（如 count_unverified）能看到新状态；
        # 测试用 session autoflush=False，不 flush 会读到旧值
        self.db.flush()
        return action

    def start_action(self, action_id: int, user_id: int) -> IssueAction:
        """assignee 或 supervisor+ 可调用（角色检查在 router）。"""
        return self._transition_action(
            action_id, ["open"], "in_progress", user_id)

    def complete_action(self, action_id: int, user_id: int) -> IssueAction:
        return self._transition_action(
            action_id, ["open", "in_progress"], "done", user_id)

    def verify_action(self, action_id: int, user_id: int) -> IssueAction:
        return self._transition_action(
            action_id, ["done"], "verified", user_id)

    # ---------- Defect 联动 ----------

    def create_from_defect(self, defect, *, reported_by_id: int) -> Issue:
        """从 DefectRecord 派生 Issue。同事务调用，失败回滚。"""
        quality_type = self.types.get_by_code("quality")
        if quality_type is None:
            raise BusinessRuleError("IssueType 'quality' 未 seed，无法联动")
        from lightmes.modules.production.models import DefectRecord  # 局部 import 避免循环
        assert isinstance(defect, DefectRecord)
        title = f"缺陷上报: {defect.defect_type_name} (SN {defect.serial_unit_id})"
        return self.create_issue(
            issue_type_id=quality_type.id,
            title=title,
            description=defect.remark or "",
            source="defect_linked",
            serial_unit_id=defect.serial_unit_id,
            work_order_id=defect.work_order_id,
            work_station_id=defect.work_station_id,
            operation_id=defect.operation_id,
            defect_id=defect.id,
            reported_by_id=reported_by_id,
        )
