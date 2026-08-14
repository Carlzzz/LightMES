from lightmes.modules.connectivity.models import MachineConnection, MachineTopic
from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.ingestor import MachineSignalIngestor
from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.issue.models import IssueType
from lightmes.modules.masterdata.models import Line, WorkStation


def _setup(db_session):
    ensure_system_downtime_reasons(db_session)
    conn = MachineConnection(name="CONN_AL", is_active=True)
    db_session.add(conn); db_session.flush()
    topic = MachineTopic(machine_connection_id=conn.id, topic_pattern="al/#",
                         payload_format="json", is_active=True)
    db_session.add(topic); db_session.flush()
    line = Line(code="L_AL", name="L_AL")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_AL", name="WS_AL", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    it = IssueType(code="equipment", name="设备", severity="major")
    db_session.add(it); db_session.flush()
    return ws, topic


def test_alarm_creates_issue_when_enabled(db_session, monkeypatch):
    from lightmes.config import get_settings

    ws, topic = _setup(db_session)
    tag = MachineTag(machine_topic_id=topic.id, name="alarm", field_path="$.a",
                     signal_type="alarm")
    db_session.add(tag); db_session.flush()

    # get_settings() is @lru_cache; mutate the cached instance so ingestor sees it
    monkeypatch.setattr(get_settings(), "equipment_auto_create_issue_on_fault", True)

    from lightmes.modules.equipment.state_machine import WorkstationStateMachine
    sm = WorkstationStateMachine(db_session)
    from datetime import datetime, timezone
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    MachineSignalIngestor(db_session).ingest(tag, "E-stop triggered", ws.id)

    from lightmes.modules.issue.models import Issue
    from sqlalchemy import select
    issue = db_session.execute(select(Issue)).scalars().first()
    assert issue is not None
    assert issue.source == "station_andon"


def test_alarm_no_issue_when_disabled(db_session):
    ws, topic = _setup(db_session)
    tag = MachineTag(machine_topic_id=topic.id, name="alarm", field_path="$.a",
                     signal_type="alarm")
    db_session.add(tag); db_session.flush()

    from lightmes.modules.equipment.state_machine import WorkstationStateMachine
    sm = WorkstationStateMachine(db_session)
    from datetime import datetime, timezone
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    MachineSignalIngestor(db_session).ingest(tag, "E-stop triggered", ws.id)

    from lightmes.modules.issue.models import Issue
    from sqlalchemy import select
    assert db_session.execute(select(Issue)).scalars().first() is None
