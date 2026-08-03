import pytest
from lightmes.database import SessionLocal, engine
# 注册所有模型到 Base.metadata，保证跨模块 FK 在隔离测试中也能解析
from lightmes.modules.auth import models as _auth_models  # noqa: F401
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
from lightmes.modules.production import models as _production_models  # noqa: F401


@pytest.fixture()
def db_session():
    """每个测试用一个事务，结束回滚，保持隔离。

    注意：不显式 connection.begin()。handler 内的 db.rollback()（如 HTMX 扫码页
    出错回滚）会回滚并解绑 connection 上的事务，若先 begin 外层事务，收尾时
    trans.rollback() 会触发 "transaction already deassociated" 警告。绑定后由
    Session.close() 负责回滚 pending 事务，保持隔离即可。
    """
    connection = engine.connect()
    session = SessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        connection.close()
