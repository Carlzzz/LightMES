from lightmes.modules.equipment.monitor_service import MonitorService
from lightmes.modules.masterdata.models import Line, WorkStation
from lightmes.modules.connectivity.models import MachineConnection


def test_monitor_board_returns_station_with_state_and_conn(db_session):
    line = Line(code="L_MB", name="L_MB")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_MB", name="WS_MB", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    conn = MachineConnection(name="C_MB", protocol="mqtt",
                             work_station_id=ws.id, status="connected")
    db_session.add(conn); db_session.flush()

    board = MonitorService(db_session).monitor_board()
    assert len(board) == 1
    row = board[0]
    assert row["work_station_id"] == ws.id
    assert row["code"] == "WS_MB"
    assert row["state"] is None  # no state recorded yet
    assert row["conn_status"] == "connected"


def test_monitor_board_includes_state(db_session):
    from lightmes.modules.equipment import ensure_system_downtime_reasons
    from lightmes.modules.equipment.state_machine import WorkstationStateMachine
    from datetime import datetime, timezone

    ensure_system_downtime_reasons(db_session)
    line = Line(code="L_MB2", name="L_MB2")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_MB2", name="WS_MB2", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    board = MonitorService(db_session).monitor_board()
    assert board[0]["state"] == "RUNNING"
