import pytest
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import SkillCreate
from lightmes.modules.auth.models import User


def _user(db_session, uname="op"):
    u = User(username=uname, password_hash="x", display_name=uname)
    db_session.add(u); db_session.flush(); return u


def test_create_skill_and_list(db_session):
    svc = SkillService(db_session)
    s = svc.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    assert s.id is not None
    assert [x.code for x in svc.list_skills()] == ["ASSY"]


def test_create_skill_dup_code_raises(db_session):
    svc = SkillService(db_session)
    svc.create_skill(SkillCreate(code="DUP", name="a", max_level=3))
    with pytest.raises(ValueError):
        svc.create_skill(SkillCreate(code="DUP", name="b", max_level=3))


def test_set_operator_skill_creates_then_updates(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK", name="技能", max_level=3))
    os1 = svc.set_operator_skill(u.id, s.id, 2)
    assert os1.level == 2
    os2 = svc.set_operator_skill(u.id, s.id, 3)  # upsert → 更新
    assert os2.id == os1.id and os2.level == 3
    assert len(svc.list_operator_skills()) == 1


def test_set_operator_skill_level_out_of_range_raises(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK2", name="技能", max_level=3))
    with pytest.raises(ValueError):
        svc.set_operator_skill(u.id, s.id, 4)  # >max_level
    with pytest.raises(ValueError):
        svc.set_operator_skill(u.id, s.id, 0)  # <1


def test_set_operator_skill_unknown_user_or_skill_raises(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK3", name="技能", max_level=3))
    with pytest.raises(ValueError):
        svc.set_operator_skill(99999, s.id, 1)
    with pytest.raises(ValueError):
        svc.set_operator_skill(u.id, 99999, 1)


def test_get_operator_level(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK4", name="技能", max_level=3))
    assert svc.get_operator_level(u.id, s.id) is None
    svc.set_operator_skill(u.id, s.id, 2)
    assert svc.get_operator_level(u.id, s.id) == 2
