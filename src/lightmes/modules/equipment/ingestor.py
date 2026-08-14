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
        if not value:
            return
        cur = self.state_machine.current(work_station_id)
        if cur is not None:
            meta = dict(cur.metadata_ or {})
            meta["alarm"] = value
            cur.metadata_ = meta
            self.db.flush()
        from lightmes.config import get_settings
        if get_settings().equipment_auto_create_issue_on_fault:
            self._create_alarm_issue(tag, value, work_station_id)

    def _create_alarm_issue(self, tag, value, work_station_id):
        # Task 8 fills this in; placeholder-free stub raises nothing for now.
        logger.info("alarm auto-issue enabled but not yet implemented (tag=%s)", tag.name)


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
