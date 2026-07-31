import pytest
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate


def test_create_user_hashes_password(db_session):
    svc = AuthService(db_session)
    user = svc.create_user(
        UserCreate(username="bob", password="pw12345", display_name="Bob")
    )
    assert user.id is not None
    assert user.password_hash != "pw12345"


def test_authenticate_success(db_session):
    svc = AuthService(db_session)
    svc.create_user(
        UserCreate(username="carol", password="pw12345", display_name="Carol")
    )
    result = svc.authenticate("carol", "pw12345")
    assert result is not None
    assert result.username == "carol"


def test_authenticate_wrong_password(db_session):
    svc = AuthService(db_session)
    svc.create_user(
        UserCreate(username="dave", password="pw12345", display_name="Dave")
    )
    assert svc.authenticate("dave", "wrong") is None


def test_authenticate_unknown_user(db_session):
    svc = AuthService(db_session)
    assert svc.authenticate("nobody", "pw12345") is None
