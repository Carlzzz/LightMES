from lightmes.modules.connectivity.models import MachineConnection, MachineTopic
from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.ingestor import MachineSignalIngestor
from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.masterdata.models import Line, WorkStation


def _setup(db_session):
    ensure_system_downtime_reasons(db_session)
    conn = MachineConnection(name="L_IG", is_active=True)
    db_session.add(conn); db_session.flush()
    topic = MachineTopic(machine_connection_id=conn.id, topic_pattern="ig/#",
                         payload_format="json", is_active=True)
    db_session.add(topic); db_session.flush()
    line = Line(code="L_IG", name="L_IG")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_IG", name="WS_IG", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    return ws, topic


def test_state_signal_transitions(db_session):
    ws, topic = _setup(db_session)
    tag = MachineTag(machine_topic_id=topic.id, name="state", field_path="$.s",
                     signal_type="state", transform={"value_map": {"1": "RUNNING", "2": "FAULT"}})
    db_session.add(tag); db_session.flush()

    ing = MachineSignalIngestor(db_session)
    ing.ingest(tag, "1", ws.id)
    assert WorkstationStateMachine(db_session).current(ws.id).state == "RUNNING"
    ing.ingest(tag, "2", ws.id)
    assert WorkstationStateMachine(db_session).current(ws.id).state == "FAULT"


def test_count_signal_delta_and_reset(db_session):
    ws, topic = _setup(db_session)
    tag = MachineTag(machine_topic_id=topic.id, name="good", field_path="$.g",
                     signal_type="good_count")
    db_session.add(tag); db_session.flush()

    ing = MachineSignalIngestor(db_session)
    ing.ingest(tag, 100, ws.id)
    assert tag.last_count_value == 100
    ing.ingest(tag, 105, ws.id)
    assert tag.last_count_value == 105
    # reset (device reboot)
    ing.ingest(tag, 3, ws.id)
    assert tag.last_count_value == 3


def test_telemetry_writes_metadata(db_session):
    ws, topic = _setup(db_session)
    tag = MachineTag(machine_topic_id=topic.id, name="temp", field_path="$.t",
                     signal_type="telemetry", unit="C")
    db_session.add(tag); db_session.flush()
    sm = WorkstationStateMachine(db_session)
    from datetime import datetime, timezone
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    ing = MachineSignalIngestor(db_session)
    ing.ingest(tag, 72.5, ws.id)
    cur = sm.current(ws.id)
    assert cur.metadata_["temp"] == 72.5
    assert cur.metadata_["temp_unit"] == "C"
