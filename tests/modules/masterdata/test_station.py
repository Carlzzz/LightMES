import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import StationCreate


def test_create_station_persists(db_session):
    svc = MasterDataService(db_session)
    s = svc.create_station(StationCreate(code="ST-01", name="装配1"))
    assert s.id is not None
    assert s.is_active is True


def test_create_station_duplicate_code_rejected(db_session):
    svc = MasterDataService(db_session)
    svc.create_station(StationCreate(code="ST-DUP", name="x"))
    with pytest.raises(ValueError):
        svc.create_station(StationCreate(code="ST-DUP", name="y"))
