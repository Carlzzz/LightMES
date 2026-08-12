import pytest
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.auth.models import ApiKey, User
from lightmes.shared.security import verify_password


def _user(db_session, username="apiuser"):
    u = User(username=username, password_hash="x", display_name="U", is_active=True)
    db_session.add(u); db_session.flush()
    return u


def test_api_key_create_returns_full_key_and_hash(db_session):
    """创建返回 full_key（明文一次）+ ApiKey 记录（hash 不含明文）。"""
    u = _user(db_session)
    svc = ApiKeyService(db_session)
    full_key, record = svc.create(name="test", user_id=u.id, scopes=["read", "write"])
    assert full_key.startswith("lmk_live_")
    assert len(full_key) > 30
    assert record.name == "test"
    assert record.user_id == u.id
    assert record.scopes == ["read", "write"]
    assert record.key_hash != full_key  # hash not plaintext
    assert verify_password(full_key, record.key_hash)
    assert record.key_prefix == full_key[:12]


def test_api_key_create_test_prefix(db_session):
    """test=True 返回 lmk_test_ 前缀。"""
    u = _user(db_session)
    full_key, _ = ApiKeyService(db_session).create(
        name="t", user_id=u.id, scopes=["read"], test=True)
    assert full_key.startswith("lmk_test_")


def test_api_key_validate_valid_key(db_session):
    """有效 key 返回 (User, ApiKey)。"""
    u = _user(db_session)
    full_key, record = ApiKeyService(db_session).create(
        name="t", user_id=u.id, scopes=["read"])
    user_out, key_out = ApiKeyService(db_session).validate(full_key)
    assert user_out.id == u.id
    assert key_out.id == record.id


def test_api_key_validate_invalid_format_raises_401(db_session):
    """不带 lmk_ 前缀 → 401。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ApiKeyService(db_session).validate("garbage_key_no_prefix")
    assert exc.value.status_code == 401


def test_api_key_validate_revoked_raises_401(db_session):
    """已吊销 → 401。"""
    u = _user(db_session)
    full_key, record = ApiKeyService(db_session).create(
        name="t", user_id=u.id, scopes=["read"])
    ApiKeyService(db_session).revoke(record.id, revoked_by_user_id=u.id)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ApiKeyService(db_session).validate(full_key)
    assert exc.value.status_code == 401


def test_api_key_validate_expired_raises_401(db_session):
    """expires_at 过去 → 401。"""
    from datetime import datetime, timedelta
    u = _user(db_session)
    full_key, _ = ApiKeyService(db_session).create(
        name="t", user_id=u.id, scopes=["read"],
        expires_at=datetime.now() - timedelta(hours=1))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ApiKeyService(db_session).validate(full_key)
    assert exc.value.status_code == 401


def test_api_key_revoke_sets_revoked_at(db_session):
    u = _user(db_session)
    _, record = ApiKeyService(db_session).create(name="t", user_id=u.id, scopes=["read"])
    ApiKeyService(db_session).revoke(record.id, revoked_by_user_id=u.id)
    db_session.refresh(record)
    assert record.revoked_at is not None
    assert record.is_active is False
