import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import lightmes.database as db_module
from lightmes.config import get_settings
from lightmes.database import create_engine
# 注册所有模型到 Base.metadata，保证跨模块 FK 在隔离测试中也能解析
from lightmes.modules.auth import models as _auth_models  # noqa: F401
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
from lightmes.modules.production import models as _production_models  # noqa: F401
from lightmes.modules.api_v1 import models as _api_v1_models  # noqa: F401
from lightmes.modules.connectivity import models as _connectivity_models  # noqa: F401
from lightmes.modules.equipment import models as _equipment_models  # noqa: F401
from lightmes.modules.issue import models as _issue_models  # noqa: F401


_settings = get_settings()
if _settings.test_database_url and _settings.environment != "production":
    _test_engine = create_engine(_settings.test_database_url, pool_pre_ping=True)
    _test_session_local = sessionmaker(
        bind=_test_engine, autoflush=False, expire_on_commit=False
    )
    _using_dedicated_test_db = True
else:
    _test_engine = db_module.engine
    _test_session_local = db_module.SessionLocal
    _using_dedicated_test_db = False

db_module.engine = _test_engine
db_module.SessionLocal = _test_session_local


@pytest.fixture(scope="session", autouse=True)
def clean_test_database():
    from sqlalchemy import text

    from lightmes.shared.base import Base

    if get_settings().environment == "production":
        pytest.fail("Refusing to truncate a production database")

    if _using_dedicated_test_db:
        Base.metadata.create_all(bind=_test_engine)

    with _test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
    os.environ["LIGHTMES_TEST_DB_TRUNCATED"] = "1"


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
    connection = _test_engine.connect()
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


@pytest.fixture
def sample_user(db_session):
    """提供测试用的已登录 user。"""
    from lightmes.modules.auth.models import User, Role
    role = db_session.execute(
        select(Role).where(Role.name == "admin")
    ).scalar_one_or_none()
    if role is None:
        role = Role(name="admin", display_name="admin", description="admin")
        db_session.add(role); db_session.flush()
    user = User(
        username="_test_sample_user", password_hash="x",
        display_name="Test", role_id=role.id, is_active=True,
    )
    db_session.add(user); db_session.flush()
    return user


@pytest.fixture
def full_station_setup(db_session, sample_user):
    """提供完整的过站上下文：Product + Routing + Operation + Line + WorkStation + WorkOrder + SerialUnit。"""
    from dataclasses import dataclass
    from lightmes.modules.masterdata.models import (
        Product, Routing, Operation, Line, WorkStation,
    )
    from lightmes.modules.production.models import WorkOrder, SerialUnit

    product = Product(code="P1", name="P1", type="finished")
    db_session.add(product); db_session.flush()
    line = Line(code="L1", name="L1")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS1", name="WS1", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    routing = Routing(code="R1", name="R1", product_id=product.id, status="active")
    db_session.add(routing); db_session.flush()
    op = Operation(seq=10, code="OP10", name="OP10", routing_id=routing.id,
                   default_work_station_id=ws.id)
    db_session.add(op); db_session.flush()
    wo = WorkOrder(code="WO1", product_id=product.id, routing_id=routing.id,
                   line_id=line.id, qty=10, status="released")
    db_session.add(wo); db_session.flush()
    su = SerialUnit(sn="SN_TEST_001", work_order_id=wo.id, product_id=product.id,
                    current_operation_seq=0, status="in_process")
    db_session.add(su); db_session.flush()

    @dataclass
    class Setup:
        product: Product
        line: Line
        work_station: WorkStation
        work_station_id: int
        routing: Routing
        operation: Operation
        work_order: WorkOrder
        serial_unit: SerialUnit

    return Setup(product, line, ws, ws.id, routing, op, wo, su)
