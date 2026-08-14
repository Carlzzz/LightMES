from datetime import datetime, timedelta, timezone

from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.masterdata.models import Line, WorkStation

T0 = datetime(2026, 8, 14, 8, 0, 0, tzinfo=timezone.utc)


def _ws(db_session):
    line = Line(code="L_EQ", name="L_EQ")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_EQ", name="WS_EQ", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    return ws


def test_transition_opens_new_state(db_session):
    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    st = sm.transition(ws.id, "RUNNING", at=T0)
    assert st.state == "RUNNING"
    assert st.ended_at is None
    assert sm.current(ws.id).state == "RUNNING"


def test_transition_closes_previous(db_session):
    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "IDLE", at=T0 + timedelta(seconds=60))
    cur = sm.current(ws.id)
    assert cur.state == "IDLE"
    # previous closed with duration
    prev = db_session.query(
        type(cur)).filter_by(work_station_id=ws.id, state="RUNNING").one()
    assert prev.ended_at is not None
    assert prev.duration_seconds == 60


def test_same_state_noop_merges_metadata(db_session):
    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0, metadata={"a": 1})
    st = sm.transition(ws.id, "RUNNING", at=T0 + timedelta(seconds=5), metadata={"b": 2})
    # same row, no new row
    assert st.metadata_ == {"a": 1, "b": 2}
    rows = db_session.query(type(st)).filter_by(work_station_id=ws.id).all()
    assert len(rows) == 1


def test_fault_opens_unplanned_downtime(db_session):
    from lightmes.modules.equipment.models import ProductionDowntime

    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "FAULT", at=T0 + timedelta(minutes=10))
    dt = db_session.query(ProductionDowntime).filter_by(work_station_id=ws.id).one()
    assert dt.ended_at is None
    assert dt.is_planned is False
    assert dt.downtime_reason.code == "AUTO-FAULT"


def test_leaving_fault_closes_downtime(db_session):
    from lightmes.modules.equipment.models import ProductionDowntime

    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "FAULT", at=T0 + timedelta(minutes=10))
    sm.transition(ws.id, "RUNNING", at=T0 + timedelta(minutes=25))
    dt = db_session.query(ProductionDowntime).filter_by(work_station_id=ws.id).one()
    assert dt.ended_at is not None
    assert dt.duration_minutes == 15


def test_maintenance_is_planned(db_session):
    from lightmes.modules.equipment.models import ProductionDowntime

    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "MAINTENANCE", at=T0 + timedelta(minutes=10))
    dt = db_session.query(ProductionDowntime).filter_by(work_station_id=ws.id).one()
    assert dt.is_planned is True
    assert dt.downtime_reason.code == "AUTO-MAINT"
