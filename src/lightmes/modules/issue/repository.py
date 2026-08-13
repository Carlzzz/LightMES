from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lightmes.modules.issue.models import Issue, IssueAction, IssueType

# status 集合：open / acknowledged 视为"未关闭"（仍计入 open / blocking 计数）
_UNCLOSED_STATUSES = ("open", "acknowledged")


class IssueTypeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_active(self) -> list[IssueType]:
        return list(self.db.execute(
            select(IssueType)
            .where(IssueType.is_active.is_(True))
            .order_by(IssueType.severity.desc(), IssueType.code)
        ).scalars().all())

    def get(self, type_id: int) -> IssueType | None:
        return self.db.get(IssueType, type_id)

    def get_by_code(self, code: str) -> IssueType | None:
        return self.db.execute(
            select(IssueType).where(IssueType.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[IssueType]:
        return list(self.db.execute(
            select(IssueType).order_by(IssueType.code)
        ).scalars().all())


class IssueRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, issue_id: int) -> Issue | None:
        return self.db.get(Issue, issue_id)

    def list(
        self,
        *,
        statuses: list[str] | None = None,
        severities: list[str] | None = None,
        sources: list[str] | None = None,
        work_station_id: int | None = None,
        work_order_id: int | None = None,
        serial_unit_id: int | None = None,
        reported_by_id: int | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> list[Issue]:
        q = select(Issue).order_by(Issue.status, Issue.id.desc())
        if statuses:
            q = q.where(Issue.status.in_(statuses))
        if severities:
            q = q.where(Issue.severity.in_(severities))
        if sources:
            q = q.where(Issue.source.in_(sources))
        if work_station_id is not None:
            q = q.where(Issue.work_station_id == work_station_id)
        if work_order_id is not None:
            q = q.where(Issue.work_order_id == work_order_id)
        if serial_unit_id is not None:
            q = q.where(Issue.serial_unit_id == serial_unit_id)
        if reported_by_id is not None:
            q = q.where(Issue.reported_by_id == reported_by_id)
        if search:
            q = q.where(Issue.title.ilike(f"%{search}%"))
        return list(self.db.execute(
            q.offset((page - 1) * size).limit(size)
        ).scalars().all())

    def count_open_blocking_for_sn(self, serial_unit_id: int) -> int:
        """返回该 SN 当前阻断中的 issue 数量。"""
        return self.db.execute(
            select(func.count())
            .select_from(Issue)
            .join(IssueType)
            .where(
                Issue.serial_unit_id == serial_unit_id,
                Issue.status.in_(_UNCLOSED_STATUSES),
                IssueType.is_blocking.is_(True),
            )
        ).scalar_one()

    def get_blocking_for_sn(self, serial_unit_id: int) -> Issue | None:
        """返回最新的阻断 issue，无则 None。"""
        return self.db.execute(
            select(Issue)
            .join(IssueType)
            .where(
                Issue.serial_unit_id == serial_unit_id,
                Issue.status.in_(_UNCLOSED_STATUSES),
                IssueType.is_blocking.is_(True),
            )
            .order_by(Issue.id.desc())
            .limit(1)
        ).scalars().first()

    def count_open(self) -> int:
        return self.db.execute(
            select(func.count()).select_from(Issue)
            .where(Issue.status.in_(_UNCLOSED_STATUSES))
        ).scalar_one()

    def count_blocking(self) -> int:
        return self.db.execute(
            select(func.count())
            .select_from(Issue)
            .join(IssueType)
            .where(
                Issue.status.in_(_UNCLOSED_STATUSES),
                IssueType.is_blocking.is_(True),
            )
        ).scalar_one()

    def add(self, issue: Issue) -> Issue:
        self.db.add(issue)
        self.db.flush()
        return issue


class IssueActionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, action_id: int) -> IssueAction | None:
        return self.db.get(IssueAction, action_id)

    def list_for_issue(self, issue_id: int) -> list[IssueAction]:
        return list(self.db.execute(
            select(IssueAction)
            .where(IssueAction.issue_id == issue_id)
            .order_by(IssueAction.id)
        ).scalars().all())

    def count_unverified(self, issue_id: int) -> int:
        return self.db.execute(
            select(func.count()).select_from(IssueAction)
            .where(
                IssueAction.issue_id == issue_id,
                IssueAction.status != "verified",
            )
        ).scalar_one()

    def add(self, action: IssueAction) -> IssueAction:
        self.db.add(action)
        self.db.flush()
        return action
