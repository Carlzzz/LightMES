from lightmes.modules.equipment.router import _oee_rows


def test_oee_rows_returns_station_rows(db_session):
    from lightmes.modules.masterdata.models import Line, WorkStation

    line = Line(code="L_OB", name="L_OB")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_OB", name="WS_OB", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()

    rows = _oee_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "WS_OB"
    assert row["state"] == "未采集"
    # quality is None (no work order) → displayed as "N/A"
    assert row["quality"] is None
