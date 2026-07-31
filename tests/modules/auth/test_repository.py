from lightmes.modules.auth.models import User


def test_user_can_be_persisted(db_session):
    user = User(
        username="alice",
        password_hash="x",
        display_name="Alice",
        role="admin",
    )
    db_session.add(user)
    db_session.flush()
    assert user.id is not None
    assert user.is_active is True
    assert user.created_at is not None
