import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import LineCreate, WorkStationCreate
from lightmes.modules.masterdata.query_service import MasterDataQueryService


def test_create_line_and_work_stations_ordered(db_session):
    svc = MasterDataService(db_session)
    line = svc.create_line(LineCreate(code="LINE-1", name="总装线1"))
    assert line.id is not None
    ws2 = svc.create_work_station(WorkStationCreate(
        code="WS-2", name="装配站", line_id=line.id, seq=2))
    ws1 = svc.create_work_station(WorkStationCreate(
        code="WS-1", name="上料站", line_id=line.id, seq=1))
    stations = svc.work_stations.list_by_line(line.id)
    assert [w.seq for w in stations] == [1, 2]
    assert stations[0].code == "WS-1"


def test_duplicate_line_code_rejected(db_session):
    svc = MasterDataService(db_session)
    svc.create_line(LineCreate(code="DUP-L", name="x"))
    with pytest.raises(ValueError):
        svc.create_line(LineCreate(code="DUP-L", name="y"))


def test_work_station_unknown_line_rejected(db_session):
    svc = MasterDataService(db_session)
    with pytest.raises(ValueError):
        svc.create_work_station(WorkStationCreate(
            code="WS-X", name="x", line_id=999999, seq=1))


def test_facade_get_line_and_station(db_session):
    svc = MasterDataService(db_session)
    line = svc.create_line(LineCreate(code="LINE-Q", name="查询线"))
    ws = svc.create_work_station(WorkStationCreate(
        code="WS-Q", name="站", line_id=line.id, seq=1))
    q = MasterDataQueryService(db_session)
    assert q.get_line(line.id).code == "LINE-Q"
    assert q.get_work_station(ws.id).code == "WS-Q"
    assert q.get_line(999999) is None
