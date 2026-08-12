import pytest
from sqlalchemy.orm import sessionmaker

import lightmes.database as db_module
from lightmes.database import SessionLocal, engine
# 注册所有模型到 Base.metadata，保证跨模块 FK 在隔离测试中也能解析
from lightmes.modules.auth import models as _auth_models  # noqa: F401
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
from lightmes.modules.production import models as _production_models  # noqa: F401
from lightmes.modules.api_v1 import models as _api_v1_models  # noqa: F401
from lightmes.modules.connectivity import models as _connectivity_models  # noqa: F401


@pytest.fixture()
def db_session(monkeypatch):
    """每个测试用外层事务包裹 + SAVEPOINT，结束回滚，保持隔离。

    支持 service 内显式 `db.commit()`（如 operation_pass_service 5c failed 分支
    要保留 defect + quarantined SN）：通过 SAVEPOINT 拦截 session.commit()，
    让其退化为 RELEASE SAVEPOINT，外层事务始终不被提交，测试间互不影响。

    兼容 service 内 `db.rollback()`：sqlalchemy 在 SAVEPOINT 模式下，rollback
    会回滚到上一个 savepoint（而非整事务），不会解绑 connection 上的外层事务。

    同时 monkeypatch `lightmes.database.SessionLocal` 为绑定到当前 connection
    的 sessionmaker，让 service 层内显式 `SessionLocal()`（如 mqtt_listener 的
    persist_message / reconcile）也能加入同一事务，看到 fixture 的 savepoint
    已提交数据，且不会污染真实数据库。
    """
    connection = engine.connect()
    trans = connection.begin()
    test_sessionmaker = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint",
        autoflush=False, expire_on_commit=False,
    )
    session = test_sessionmaker()
    original_sessionlocal = db_module.SessionLocal
    monkeypatch.setattr(db_module, "SessionLocal", test_sessionmaker)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
        monkeypatch.setattr(db_module, "SessionLocal", original_sessionlocal)
