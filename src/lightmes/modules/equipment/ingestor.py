import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.equipment.tag_service import TagService

logger = logging.getLogger(__name__)


class MachineSignalIngestor:
    """Route a normalized signal value to its domain effect, by signal_type."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tags = TagService(db)
        self.state_machine = WorkstationStateMachine(db)

    def ingest(self, tag: MachineTag, raw_value, work_station_id: int) -> None:
        value = self.tags.apply_transform(tag, raw_value)
        st = tag.signal_type
        if st == "state":
            self._ingest_state(tag, value, work_station_id)
        elif st in ("good_count", "reject_count", "cycle_complete"):
            self._ingest_count(tag, value)
        elif st == "telemetry":
            self._ingest_telemetry(tag, value, work_station_id)
        elif st == "alarm":
            self._ingest_alarm(tag, value, work_station_id)
        else:
            logger.warning("未知 signal_type: %s", st)

    def _ingest_state(self, tag, value, work_station_id):
        if not isinstance(value, str):
            logger.warning("state 信号非字符串，跳过 tag=%s value=%r", tag.name, value)
            return
        self.state_machine.transition(work_station_id, value, source="machine")

    def _ingest_count(self, tag, value):
        try:
            current = int(value)
        except (TypeError, ValueError):
            logger.warning("count 信号非数值，跳过 tag=%s value=%r", tag.name, value)
            return
        last = tag.last_count_value
        if last is not None and current < last:
            logger.info("计数清零（设备重启）tag=%s: %s -> %s", tag.name, last, current)
        tag.last_count_value = current
        self.db.flush()

    def _ingest_telemetry(self, tag, value, work_station_id):
        cur = self.state_machine.current(work_station_id)
        if cur is None:
            return
        meta = dict(cur.metadata_ or {})
        meta[tag.name] = value
        if tag.unit:
            meta[f"{tag.name}_unit"] = tag.unit
        cur.metadata_ = meta
        self.db.flush()

    def _ingest_alarm(self, tag, value, work_station_id):
        cur = self.state_machine.current(work_station_id)
        if cur is not None:
            meta = dict(cur.metadata_ or {})
            if value:
                meta["alarm"] = value
            else:
                meta.pop("alarm", None)
            cur.metadata_ = meta
            self.db.flush()
        from lightmes.config import get_settings
        if not get_settings().equipment_auto_create_issue_on_fault:
            return
        if value:
            self._create_alarm_issue(tag, value, work_station_id)
        else:
            self._resolve_alarm_issues(tag, work_station_id)

    def _create_alarm_issue(self, tag, value, work_station_id):
        from sqlalchemy import select

        from lightmes.modules.auth.models import User
        from lightmes.modules.issue.models import Issue, IssueType
        from lightmes.modules.issue.repository import IssueTypeRepository
        from lightmes.modules.issue.service import IssueService

        issue_type = IssueTypeRepository(self.db).get_by_code("equipment")
        if issue_type is None:
            logger.warning("缺少 equipment issue type，跳过自动建 issue")
            return
        # 去重：同一 tag 同一工位的报警在未关闭期间只建一条 issue
        title = f"设备告警: {tag.name} = {value}"
        if len(title) > 200:
            title = title[:200]
        dup = self.db.execute(
            select(Issue)
            .join(IssueType, Issue.issue_type_id == IssueType.id)
            .where(
                Issue.work_station_id == work_station_id,
                Issue.status.in_(("open", "acknowledged", "resolved")),
                Issue.title == title,
            )
            .order_by(Issue.id.desc())
            .limit(1)
        ).scalars().first()
        if dup is not None:
            return
        # reported_by_id 是 NOT NULL FK；机器上报无真人，复用 _system_machine 占位用户
        user = self.db.execute(
            select(User).where(User.username == "_system_machine")
        ).scalar_one_or_none()
        if user is None:
            user = User(
                username="_system_machine", password_hash="!",
                display_name="设备自动上报", is_active=True,
            )
            self.db.add(user)
            self.db.flush()
        IssueService(self.db).create_issue(
            issue_type_id=issue_type.id,
            title=title,
            description=f"工位 {work_station_id} 告警：{tag.name}={value}（持续中，重复信号不另建单）",
            source="station_andon",
            work_station_id=work_station_id,
            reported_by_id=user.id,
        )
        self.db.flush()

    def _resolve_alarm_issues(self, tag, work_station_id):
        """报警恢复：把该 tag 在本工位未关闭的报警 issue 自动 resolve+close。"""
        from datetime import datetime

        from sqlalchemy import select

        from lightmes.modules.auth.models import User
        from lightmes.modules.issue.models import Issue
        from lightmes.modules.issue.service import IssueService

        prefix = f"设备告警: {tag.name}"
        open_issues = self.db.execute(
            select(Issue).where(
                Issue.work_station_id == work_station_id,
                Issue.status.in_(("open", "acknowledged")),
                Issue.title.like(f"{prefix}%"),
            )
        ).scalars().all()
        if not open_issues:
            return
        user = self.db.execute(
            select(User).where(User.username == "_system_machine")
        ).scalar_one_or_none()
        if user is None:
            return
        svc = IssueService(self.db)
        now = datetime.now()
        for issue in open_issues:
            if issue.status == "open":
                issue.acknowledged_by_id = user.id
                issue.acknowledged_at = now
                issue.status = "acknowledged"
            issue.status = "resolved"
            issue.root_cause = issue.root_cause or "设备报警信号恢复（自动闭环）"
            issue.containment_action = issue.containment_action or "报警恢复信号触发自动关闭"
            issue.disposition = "use_as_is"
            issue.resolved_by_id = user.id
            issue.resolved_at = now
            issue.status = "closed"
            issue.closed_by_id = user.id
            issue.closed_at = now
            issue.resolution_notes = "报警恢复，系统自动关闭"
        self.db.flush()
        logger.info("报警恢复：tag=%s work_station=%s 自动关闭 %d 条 issue",
                    tag.name, work_station_id, len(open_issues))


def ingest_topic_signals(db: Session, topic_id: int, parsed_data: dict,
                         work_station_id: int | None) -> None:
    """Extract and ingest all active signal tags for a topic. Never raises."""
    if work_station_id is None:
        return
    tags = TagService(db).list_active_for_topic(topic_id)
    if not tags:
        return
    from lightmes.modules.connectivity.parser import MqttMessageParser

    parser = MqttMessageParser()
    ingestor = MachineSignalIngestor(db)
    for tag in tags:
        try:
            raw = parser.resolve_path(tag.field_path, parsed_data)
            ingestor.ingest(tag, raw, work_station_id)
        except Exception as e:
            logger.warning("信号 ingest 失败 tag=%s: %s", tag.name, e)
