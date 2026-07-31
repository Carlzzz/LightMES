import pytest
from lightmes.database import SessionLocal, engine


@pytest.fixture()
def db_session():
    """每个测试用一个事务，结束回滚，保持隔离。"""
    connection = engine.connect()
    trans = connection.begin()
    session = SessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
