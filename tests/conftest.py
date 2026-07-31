import pytest
from lightmes.database import SessionLocal, engine
# 注册所有模型到 Base.metadata，保证跨模块 FK 在隔离测试中也能解析
from lightmes.modules.auth import models as _auth_models  # noqa: F401
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
from lightmes.modules.production import models as _production_models  # noqa: F401


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
