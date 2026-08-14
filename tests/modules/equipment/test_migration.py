from sqlalchemy import inspect


def test_equipment_tables_exist(db_session):
    from lightmes.database import engine

    names = set(inspect(engine).get_table_names())
    for t in ("machine_tags", "workstation_states", "production_downtimes", "downtime_reasons"):
        assert t in names, f"missing table {t}"


def test_machine_connections_has_work_station_id(db_session):
    from sqlalchemy import inspect
    from lightmes.database import engine

    cols = {c["name"] for c in inspect(engine).get_columns("machine_connections")}
    assert "work_station_id" in cols


def test_ensure_system_downtime_reasons(db_session):
    from lightmes.modules.equipment import ensure_system_downtime_reasons
    from lightmes.modules.equipment.models import DowntimeReason
    from sqlalchemy import select

    ensure_system_downtime_reasons(db_session)
    db_session.flush()
    codes = set(db_session.execute(select(DowntimeReason.code)).scalars().all())
    assert {"AUTO-FAULT", "AUTO-STOP", "AUTO-WAIT", "AUTO-CLEAN", "AUTO-MAINT"} <= codes
    # idempotent
    ensure_system_downtime_reasons(db_session)
    count = db_session.execute(select(DowntimeReason)).scalars().all()
    assert len([r for r in count if r.code.startswith("AUTO-")]) == 5
