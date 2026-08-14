from datetime import datetime, timezone

from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message
from lightmes.modules.connectivity.models import MachineConnection, MachineTopic
from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.masterdata.models import Line, WorkStation


def test_end_to_end_signal_ingest(db_session):
    ensure_system_downtime_reasons(db_session)
    line = Line(code="L_PIPE", name="L_PIPE")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_PIPE", name="WS_PIPE", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()

    conn = MachineConnection(name="C_PIPE", protocol="mqtt", work_station_id=ws.id)
    db_session.add(conn); db_session.flush()
    topic = MachineTopic(machine_connection_id=conn.id, topic_pattern="press/+/state",
                         payload_format="json")
    db_session.add(topic); db_session.flush()
    tag = MachineTag(machine_topic_id=topic.id, name="state", field_path="$.state",
                     signal_type="state", transform={"value_map": {"1": "RUNNING", "2": "FAULT"}})
    db_session.add(tag); db_session.flush()
    db_session.commit()

    result = persist_message(
        connection_id=conn.id, topic="press/1/state",
        payload=b'{"state": "2"}',
        received_at=datetime.now(timezone.utc),
    )
    assert result.status == "ok"

    cur = WorkstationStateMachine(db_session).current(ws.id)
    assert cur is not None
    assert cur.state == "FAULT"
