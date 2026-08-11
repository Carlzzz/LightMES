import pytest
from lightmes.modules.production.shift_service import ShiftService
from lightmes.modules.production.schemas import ShiftCreate, ShiftUpdate
from lightmes.shared.errors import BusinessRuleError, ValidationError


_HHMM = r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$"


def test_shift_create_valid(db_session):
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="S1", name="早班", start_time="06:00", end_time="14:00"))
    assert s.id is not None
    assert s.code == "S1"


def test_shift_create_rejects_bad_time_format(db_session):
    svc = ShiftService(db_session)
    with pytest.raises(ValidationError):
        svc.create(ShiftCreate(code="S2", name="x", start_time="6am", end_time="14:00"))


def test_shift_create_rejects_bad_days_of_week(db_session):
    svc = ShiftService(db_session)
    with pytest.raises(ValidationError):
        svc.create(ShiftCreate(code="S3", name="x", start_time="06:00", end_time="14:00",
                               days_of_week=[0, 8]))


def test_shift_create_rejects_duplicate_code(db_session):
    svc = ShiftService(db_session)
    svc.create(ShiftCreate(code="DUP", name="a", start_time="06:00", end_time="14:00"))
    with pytest.raises(BusinessRuleError):
        svc.create(ShiftCreate(code="DUP", name="b", start_time="08:00", end_time="16:00"))


def test_shift_cross_overnight_detection(db_session):
    """end < start 表示跨夜。"""
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="NITE", name="夜班", start_time="22:00", end_time="06:00"))
    assert svc.is_cross_overnight(s) is True


def test_shift_update_partial(db_session):
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="U1", name="原", start_time="06:00", end_time="14:00"))
    updated = svc.update(s.id, ShiftUpdate(name="新"))
    assert updated.name == "新"
    assert updated.start_time == "06:00"  # 未改


def test_shift_current_at_returns_active_shift(db_session):
    """当前时间在班次窗口内 → 返回该班次。"""
    svc = ShiftService(db_session)
    svc.create(ShiftCreate(code="CUR", name="全天", start_time="00:00", end_time="23:59",
                           is_active=True))
    from datetime import datetime
    now = datetime(2026, 8, 11, 10, 0)  # 上午 10 点
    current = svc.current_at(line_id=None, now=now)
    assert current is not None
    assert current.code == "CUR"


def test_shift_delete(db_session):
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="DEL", name="x", start_time="06:00", end_time="14:00"))
    svc.delete(s.id)
    assert svc.list_all() == []
