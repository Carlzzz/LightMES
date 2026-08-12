import pytest
from lightmes.database import SessionLocal, engine
# 注册所有模型到 Base.metadata，保证跨模块 FK 在隔离测试中也能解析
from lightmes.modules.auth import models as _auth_models  # noqa: F401
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
from lightmes.modules.production import models as _production_models  # noqa: F401
from lightmes.modules.api_v1 import models as _api_v1_models  # noqa: F401
from lightmes.modules.connectivity import models as _connectivity_models  # noqa: F401


@pytest.fixture()
def db_session():
    """每个测试用外层事务包裹 + SAVEPOINT，结束回滚，保持隔离。

    支持 service 内显式 `db.commit()`（如 operation_pass_service 5c failed 分支
    要保留 defect + quarantined SN）：通过 SAVEPOINT 拦截 session.commit()，
    让其退化为 RELEASE SAVEPOINT，外层事务始终不被提交，测试间互不影响。

    兼容 service 内 `db.rollback()`：sqlalchemy 在 SAVEPOINT 模式下，rollback
    会回滚到上一个 savepoint（而非整事务），不会解绑 connection 上的外层事务。
    """
    connection = engine.connect()
    trans = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
