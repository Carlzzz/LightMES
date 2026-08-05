import pytest
from sqlalchemy.exc import IntegrityError
from lightmes.modules.masterdata.models import Skill, OperatorSkill
from lightmes.modules.auth.models import User


def _user(db_session, uname):
    u = User(username=uname, password_hash="x", display_name=uname)
    db_session.add(u); db_session.flush(); return u


def test_create_skill(db_session):
    s = Skill(code="ASSY", name="装配", max_level=3)
    db_session.add(s); db_session.flush()
    assert s.id is not None and s.description is None


def test_operator_skill_unique_per_user_skill(db_session):
    u = _user(db_session, "op1")
    s = Skill(code="SK1", name="技能1", max_level=3)
    db_session.add(s); db_session.flush()
    db_session.add(OperatorSkill(user_id=u.id, skill_id=s.id, level=2))
    db_session.flush()
    db_session.add(OperatorSkill(user_id=u.id, skill_id=s.id, level=3))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_same_skill_different_users_ok(db_session):
    u1 = _user(db_session, "opA"); u2 = _user(db_session, "opB")
    s = Skill(code="SK2", name="技能2", max_level=3)
    db_session.add(s); db_session.flush()
    db_session.add(OperatorSkill(user_id=u1.id, skill_id=s.id, level=1))
    db_session.add(OperatorSkill(user_id=u2.id, skill_id=s.id, level=2))
    db_session.flush()  # 无异常即通过
