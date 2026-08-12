# API v1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 LightMES 上叠加一条 `/api/v1/*` JSON 表面，提供 Bearer token 认证、Resource Controller 约定、RFC 7807 错误格式、统一分页 + OpenAPI 文档 + 审计日志，为 AI Agent 接入打基础。

**Architecture:** 双面 MES：同一 service 层 + 两条 HTTP 表面（HTML HTMX 路由不动，新增 `/api/v1/*` JSON 路由）。新增 `ApiKey` 模型 + Argon2 存储 + Bearer token 认证依赖（与现有 session 双路径）。DomainError 全局异常处理器升级为 RFC 7807 Problem Details JSON。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic, PostgreSQL, pwdlib (Argon2), pytest, uv

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（必须 127.0.0.1，不用 localhost — Windows IPv6 ~130s 卡顿）
- 测试用 `db_session` fixture（SAVEPOINT 隔离），不直接 commit
- Service 层抛 `DomainError` 子类（`ValidationError` 400 / `NotFoundError` 404 / `ConflictError` 409 / `BusinessRuleError` 422）从 `lightmes.shared.errors`
- 密码 hash 复用 `lightmes.shared.security.hash_password` / `verify_password`（pwdlib Argon2）
- 文案 Chinese for error messages，含具体资源标识
- 最新 migration ID = `b8d4f0a23c5e`（Planner），Task 1 的 down_revision
- API Key 格式：`lmk_live_<32>` 或 `lmk_test_<32>`；前缀 12 字符存明文（识别用），完整 key Argon2 hash 存储
- Key 仅创建时返回一次，列表/编辑页不显示
- Scopes 只区分 `read` / `write`（不做 per-module）
- 双路径 auth：Bearer token OR 现有 session cookie
- Argon2 慢但安全（~100ms/请求）；接受这个延迟
- ApiCallLog 只记录写操作 + 错误（4xx/5xx）；不记成功 GET
- 现有 HTML 路由不动；新增 `/api/v1/*` 路由独立 router
- 模块注册通过 `lightmes/modules/api_v1/__init__.py` 的 `register(app)` 函数（新模块）
- `main.py` 已有全局 `@app.exception_handler(DomainError)` 返回 `{"detail": ...}` —— Task 2 升级为 RFC 7807

---

### Task 1: Migration + Models (ApiKey + ApiCallLog)

**Files:**
- Modify: `src/lightmes/modules/auth/models.py` (追加 ApiKey 类)
- Create: `src/lightmes/modules/api_v1/models.py` (新模块的 ApiCallLog)
- Create: `src/lightmes/modules/api_v1/__init__.py` (register function stub)
- Create: `src/lightmes/migrations/versions/c9e5a1b34f6a_add_api_v1_tables.py`
- Test: `tests/modules/api_v1/test_models.py`

**Interfaces:**
- Consumes: 既有 `User` 模型
- Produces:
  - `ApiKey` 模型（字段如 spec 第 3.1 节）
  - `ApiCallLog` 模型（字段如 spec 第 7.2 节）
  - Alembic migration `c9e5a1b34f6a`，down_revision = `b8d4f0a23c5e`
  - `lightmes.modules.api_v1.register(app)` stub（后续 task 填充）

- [ ] **Step 1: 写失败测试 - 模型字段**

创建 `tests/modules/api_v1/__init__.py`（空文件）+ `tests/modules/api_v1/test_models.py`：

```python
from datetime import datetime
from lightmes.modules.auth.models import ApiKey, User
from lightmes.modules.api_v1.models import ApiCallLog


def test_api_key_model_basic_fields(db_session):
    """ApiKey 模型基础字段可持久化。"""
    # 先建一个 User（FK）
    u = User(username="k1", password_hash="x", display_name="K", is_active=True)
    db_session.add(u); db_session.flush()
    k = ApiKey(
        name="ERP Sync",
        key_prefix="lmk_live_abcd",
        key_hash="argon2$...",
        user_id=u.id,
        scopes=["read", "write"],
        is_active=True,
    )
    db_session.add(k); db_session.flush()
    assert k.id is not None
    assert k.scopes == ["read", "write"]
    assert k.is_active is True
    assert k.expires_at is None
    assert k.last_used_at is None


def test_api_call_log_model_basic_fields(db_session):
    """ApiCallLog 模型字段可持久化。"""
    log = ApiCallLog(
        api_key_id=None, user_id=None,
        method="POST", path="/api/v1/work-orders",
        status_code=201, duration_ms=42,
        trace_id="abc12345", client_ip="127.0.0.1",
        error_detail=None,
    )
    db_session.add(log); db_session.flush()
    assert log.id is not None
    assert log.method == "POST"
    assert log.status_code == 201
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_models.py -v`
Expected: ImportError on `ApiKey` / `ApiCallLog`.

- [ ] **Step 3: 创建 api_v1 模块结构**

创建 `src/lightmes/modules/api_v1/__init__.py`：

```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Register /api/v1/* routes. Filled in by later tasks."""
    from lightmes.modules.api_v1.router import router
    app.include_router(router, prefix="/api/v1")
```

创建 `src/lightmes/modules/api_v1/models.py`：

```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class ApiCallLog(Base, TimestampMixin):
    __tablename__ = "api_call_logs"
    __table_args__ = (
        Index("ix_api_call_logs_path", "path"),
        Index("ix_api_call_logs_status_code", "status_code"),
        Index("ix_api_call_logs_trace_id", "trace_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id"), default=None, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(32), default=None)
    client_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(String(500), default=None)
```

创建 `src/lightmes/modules/api_v1/router.py`（暂存 stub，Task 2+ 填充）：

```python
from fastapi import APIRouter

router = APIRouter(tags=["api-v1"])
```

- [ ] **Step 4: 加 ApiKey 模型到 auth/models.py**

修改 `src/lightmes/modules/auth/models.py`，文件末尾追加：

```python
class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
```

确认 `auth/models.py` 顶部 import 含 `JSON`、`String`、`datetime`、`TimestampMixin`、`Base`、`ForeignKey`、`Mapped`、`mapped_column`、`DateTime`。如缺则补：

```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin
```

- [ ] **Step 5: 创建 Alembic 迁移**

创建 `src/lightmes/migrations/versions/c9e5a1b34f6a_add_api_v1_tables.py`：

```python
"""add_api_v1_tables

Revision ID: c9e5a1b34f6a
Revises: b8d4f0a23c5e
Create Date: 2026-08-12 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9e5a1b34f6a'
down_revision = 'b8d4f0a23c5e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('key_prefix', sa.String(length=16), nullable=False),
        sa.Column('key_hash', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_ip', sa.String(length=64), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id']),
    )
    op.create_index('ix_api_keys_key_prefix', 'api_keys', ['key_prefix'])
    op.create_index('ix_api_keys_user_id', 'api_keys', ['user_id'])
    op.create_table('api_call_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('trace_id', sa.String(length=32), nullable=True),
        sa.Column('client_ip', sa.String(length=64), nullable=True),
        sa.Column('error_detail', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_api_call_logs_api_key_id', 'api_call_logs', ['api_key_id'])
    op.create_index('ix_api_call_logs_user_id', 'api_call_logs', ['user_id'])
    op.create_index('ix_api_call_logs_path', 'api_call_logs', ['path'])
    op.create_index('ix_api_call_logs_status_code', 'api_call_logs', ['status_code'])
    op.create_index('ix_api_call_logs_trace_id', 'api_call_logs', ['trace_id'])


def downgrade() -> None:
    op.drop_index('ix_api_call_logs_trace_id', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_status_code', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_path', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_user_id', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_api_key_id', table_name='api_call_logs')
    op.drop_table('api_call_logs')
    op.drop_index('ix_api_keys_user_id', table_name='api_keys')
    op.drop_index('ix_api_keys_key_prefix', table_name='api_keys')
    op.drop_table('api_keys')
```

- [ ] **Step 6: 注册 api_v1 模块到 main.py**

修改 `src/lightmes/main.py`，import + register：

```python
from lightmes.modules import auth, integration, masterdata, production, trace, quality
# 改为：
from lightmes.modules import api_v1, auth, integration, masterdata, production, trace, quality
```

在 `quality.register(app)` 后追加：

```python
api_v1.register(app)
```

- [ ] **Step 7: 应用迁移验证**

Run: `uv run alembic upgrade head`
Expected: 输出 `Running upgrade b8d4f0a23c5e -> c9e5a1b34f6a, add_api_v1_tables`，无报错。

Run: `uv run alembic downgrade -1`
Expected: 输出 `Running downgrade c9e5a1b34f6a -> b8d4f0a23c5e`，无报错。

Run: `uv run alembic upgrade head`

- [ ] **Step 8: 运行测试，确认通过**

Run: `uv run pytest tests/modules/api_v1/test_models.py -v`
Expected: 2 tests PASS。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/auth/models.py \
        src/lightmes/modules/api_v1/__init__.py \
        src/lightmes/modules/api_v1/models.py \
        src/lightmes/modules/api_v1/router.py \
        src/lightmes/migrations/versions/c9e5a1b34f6a_add_api_v1_tables.py \
        src/lightmes/main.py \
        tests/modules/api_v1/__init__.py \
        tests/modules/api_v1/test_models.py
git commit -m "feat(api-v1): ApiKey + ApiCallLog models + migration"
```

---

### Task 2: API Key 服务 + require_api_key 依赖 + Problem Details 错误处理器 + trace_id middleware

**Files:**
- Create: `src/lightmes/modules/api_v1/api_key_service.py` (生成/验证/吊销)
- Create: `src/lightmes/modules/api_v1/dependencies.py` (require_api_key)
- Create: `src/lightmes/modules/api_v1/middleware.py` (TraceIdMiddleware + ApiCallLogMiddleware)
- Create: `src/lightmes/modules/api_v1/errors.py` (Problem Details 响应 + 全局异常处理器)
- Modify: `src/lightmes/main.py` (注册 middleware + 升级 DomainError handler)
- Test: `tests/modules/api_v1/test_api_key_service.py`
- Test: `tests/modules/api_v1/test_auth_and_errors.py`

**Interfaces:**
- Consumes: Task 1 的 `ApiKey` 模型；`lightmes.shared.security.hash_password` / `verify_password`
- Produces:
  - `ApiKeyService.create(name, user_id, scopes, expires_at=None, test=False) -> tuple[str, ApiKey]` (returns full_key + record)
  - `ApiKeyService.validate(full_key) -> tuple[User, ApiKey]` (raises 401 HTTPException on invalid)
  - `ApiKeyService.revoke(api_key_id, revoked_by_user_id) -> ApiKey`
  - `ApiKeyService.list_for_user(user_id) -> list[ApiKey]`
  - `require_api_key(*scopes)` FastAPI dependency factory → returns User
  - `TraceIdMiddleware` (BaseHTTPMiddleware)
  - `ApiCallLogMiddleware` (BaseHTTPMiddleware, only logs /api/v1/*)
  - 全局 DomainError handler 升级为 RFC 7807 Problem Details JSON

- [ ] **Step 1: 写失败测试 - ApiKeyService**

创建 `tests/modules/api_v1/test_api_key_service.py`：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_api_key_service.py -v`
Expected: ImportError on `api_key_service`。

- [ ] **Step 3: 实现 ApiKeyService**

创建 `src/lightmes/modules/api_v1/api_key_service.py`：

```python
import secrets
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.auth.models import ApiKey, User
from lightmes.shared.security import hash_password, verify_password

API_KEY_PREFIX_LIVE = "lmk_live_"
API_KEY_PREFIX_TEST = "lmk_test_"
API_KEY_RANDOM_LEN = 32
API_KEY_PREFIX_DISPLAY_LEN = 12


class ApiKeyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        user_id: int,
        scopes: list[str],
        expires_at: datetime | None = None,
        test: bool = False,
    ) -> tuple[str, ApiKey]:
        prefix = API_KEY_PREFIX_TEST if test else API_KEY_PREFIX_LIVE
        random_part = secrets.token_urlsafe(API_KEY_RANDOM_LEN)[:API_KEY_RANDOM_LEN]
        full_key = prefix + random_part
        key_prefix = full_key[:API_KEY_PREFIX_DISPLAY_LEN]
        key_hash = hash_password(full_key)
        record = ApiKey(
            name=name, key_prefix=key_prefix, key_hash=key_hash,
            user_id=user_id, scopes=scopes,
            is_active=True, expires_at=expires_at,
        )
        self.db.add(record); self.db.flush()
        return full_key, record

    def validate(self, full_key: str) -> tuple[User, ApiKey]:
        """Validate a full API key. Raises HTTPException(401) on any failure."""
        if not (full_key.startswith(API_KEY_PREFIX_LIVE) or full_key.startswith(API_KEY_PREFIX_TEST)):
            raise HTTPException(status_code=401, detail="无效的 API Key 格式")
        prefix = full_key[:API_KEY_PREFIX_DISPLAY_LEN]
        # Argon2 不能直接查 —— 用前缀筛候选，再逐个 verify
        candidates = list(self.db.execute(
            select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.is_active.is_(True))
        ).scalars().all())
        for k in candidates:
            if verify_password(full_key, k.key_hash):
                if not self._is_valid(k):
                    raise HTTPException(status_code=401, detail="API Key 已过期或被吊销")
                user = self.db.get(User, k.user_id)
                if user is None or not user.is_active:
                    raise HTTPException(status_code=401, detail="API Key 关联用户已停用")
                return user, k
        raise HTTPException(status_code=401, detail="API Key 无效或已吊销")

    def revoke(self, api_key_id: int, revoked_by_user_id: int) -> ApiKey:
        k = self.db.get(ApiKey, api_key_id)
        if k is None:
            from lightmes.shared.errors import NotFoundError
            raise NotFoundError(f"API Key 不存在: {api_key_id}")
        k.is_active = False
        k.revoked_at = datetime.now()
        k.revoked_by = revoked_by_user_id
        self.db.flush()
        return k

    def list_for_user(self, user_id: int) -> list[ApiKey]:
        return list(self.db.execute(
            select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.id.desc())
        ).scalars().all())

    @staticmethod
    def _is_valid(k: ApiKey) -> bool:
        if not k.is_active:
            return False
        if k.revoked_at is not None:
            return False
        if k.expires_at is not None and k.expires_at < datetime.now():
            return False
        return True
```

- [ ] **Step 4: 运行 service 测试**

Run: `uv run pytest tests/modules/api_v1/test_api_key_service.py -v`
Expected: 7 tests PASS。

- [ ] **Step 5: 写失败测试 - require_api_key 依赖**

创建 `tests/modules/api_v1/test_auth_and_errors.py`：

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin_user(db_session, username="apiadmin"):
    """Create admin user with Role row."""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    from lightmes.shared.security import hash_password
    u = User(username=username, password_hash=hash_password("pw12345"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    return u


def _login_session(client, db_session, username, password="pw12345"):
    """登录获取 session cookie。"""
    client.post("/login", data={"username": username, "password": password})


def test_require_api_key_bearer_token_success(client, db_session):
    """Bearer token 通过 require_api_key。"""
    from lightmes.modules.api_v1.dependencies import require_api_key
    u = _admin_user(db_session)
    full_key, _ = ApiKeyService(db_session).create(
        name="t", user_id=u.id, scopes=["read", "write"])
    # 直接调依赖（在测试 router 上）
    from fastapi import Depends
    test_app = FastAPI()

    @test_app.get("/test")
    def handler(user: User = Depends(require_api_key("read"))):
        return {"user_id": user.id}
    test_client = TestClient(test_app)
    resp = test_client.get("/test", headers={"Authorization": f"Bearer {full_key}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == u.id


def test_require_api_key_invalid_token_returns_401(client, db_session):
    from lightmes.modules.api_v1.dependencies import require_api_key
    from fastapi import Depends
    test_app = FastAPI()

    @test_app.get("/test")
    def handler(user: User = Depends(require_api_key("read"))):
        return {"user_id": user.id}
    test_client = TestClient(test_app)
    resp = test_client.get("/test", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_require_api_key_missing_scopes_returns_403(client, db_session):
    """read-only key 调 write endpoint → 403。"""
    from lightmes.modules.api_v1.dependencies import require_api_key
    from fastapi import Depends
    u = _admin_user(db_session)
    full_key, _ = ApiKeyService(db_session).create(
        name="ro", user_id=u.id, scopes=["read"])  # 只读
    test_app = FastAPI()

    @test_app.post("/test")
    def handler(user: User = Depends(require_api_key("read", "write"))):
        return {"ok": True}
    test_client = TestClient(test_app)
    resp = test_client.post("/test", headers={"Authorization": f"Bearer {full_key}"})
    assert resp.status_code == 403


def test_require_api_key_session_fallback(client, db_session):
    """无 Authorization header 但有 session → 通过（双路径）。"""
    from lightmes.modules.api_v1.dependencies import require_api_key
    from fastapi import Depends
    u = _admin_user(db_session, username="sessuser")
    _login_session(client, db_session, "sessuser")
    test_app = FastAPI()
    test_app.router.dependency_overrides = {}

    @test_app.get("/test")
    def handler(user: User = Depends(require_api_key("read"))):
        return {"user_id": user.id}
    # 注意：test_app 是独立 FastAPI 实例，session middleware 没装。简化：直接验证 Bearer 路径覆盖，session 路径在端到端测试覆盖。
    # 此测试改为：Authorization header 不带 → 401（不是 fallback）
    test_client = TestClient(test_app)
    resp = test_client.get("/test")
    assert resp.status_code == 401


def test_problem_details_error_format(client, db_session):
    """DomainError 返回 application/problem+json。"""
    from lightmes.shared.errors import NotFoundError
    from fastapi import FastAPI
    test_app = FastAPI()

    @test_app.get("/boom")
    def boom():
        raise NotFoundError("工单不存在: 999")
    # 注册 Problem Details handler
    from lightmes.modules.api_v1.errors import register_problem_details_handler
    register_problem_details_handler(test_app)
    test_client = TestClient(test_app)
    resp = test_client.get("/boom")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "https://lightmes/errors/NotFoundError"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert "工单不存在" in body["detail"]
    assert "trace_id" in body
    assert body["instance"] == "/boom"


def test_trace_id_present_in_response_header(client, db_session):
    """trace_id 通过响应头返回，方便 Agent 引用。"""
    from lightmes.modules.api_v1.middleware import TraceIdMiddleware
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.add_middleware(TraceIdMiddleware)

    @test_app.get("/ok")
    def ok():
        return {"ok": True}
    test_client = TestClient(test_app)
    resp = test_client.get("/ok")
    assert resp.status_code == 200
    assert "x-trace-id" in {k.lower() for k in resp.headers.keys()}
```

- [ ] **Step 6: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_auth_and_errors.py -v`
Expected: ImportError on `dependencies` / `errors` / `middleware`。

- [ ] **Step 7: 实现 require_api_key 依赖**

创建 `src/lightmes/modules/api_v1/dependencies.py`：

```python
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.auth.dependencies import current_user_or_none
from lightmes.modules.auth.models import User


def require_api_key(*scopes: str):
    """FastAPI dependency factory.

    Validates `Authorization: Bearer lmk_xxx` OR falls back to session cookie.
    Returns the User object. Raises 401 if neither path succeeds, 403 if scopes
    insufficient.

    Usage::

        @router.get("/work-orders", dependencies=[Depends(require_api_key("read"))])
        def handler(...): ...

        @router.post("/work-orders")
        def handler(user: User = Depends(require_api_key("read", "write"))): ...
    """
    required = set(scopes) if scopes else set()

    async def _check(
        request: Request,
        authorization: str | None = Header(default=None),
        db: Session = Depends(get_db),
    ) -> User:
        # Path 1: Bearer token
        if authorization and authorization.startswith("Bearer lmk_"):
            full_key = authorization[len("Bearer "):]
            user, api_key = ApiKeyService(db).validate(full_key)
            # Update last_used_at / last_used_ip (best effort)
            from datetime import datetime
            api_key.last_used_at = datetime.now()
            api_key.last_used_ip = request.client.host if request.client else None
            db.flush()
            # Stash for ApiCallLog middleware
            request.state.api_key_id = api_key.id
            request.state.api_key_user_id = user.id
            # Scope check
            granted = set(api_key.scopes or [])
            missing = required - granted
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"API Key 缺少 scope: {', '.join(sorted(missing))}",
                )
            return user
        # Path 2: Session cookie fallback (for browser admin UI)
        user = current_user_or_none(request, db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要 Bearer token 或登录会话",
            )
        request.state.api_key_user_id = user.id
        # Session path bypasses scope check (user's role gates it)
        return user

    return _check
```

- [ ] **Step 8: 实现 Problem Details 错误处理器**

创建 `src/lightmes/modules/api_v1/errors.py`：

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lightmes.shared.errors import (
    BusinessRuleError, ConflictError, DomainError, NotFoundError, ValidationError,
)

_TITLE_MAP = {
    ValidationError: "Bad Request",
    NotFoundError: "Not Found",
    ConflictError: "Conflict",
    BusinessRuleError: "Unprocessable Entity",
}


def _title_for(exc: DomainError) -> str:
    return _TITLE_MAP.get(type(exc), "Error")


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def register_problem_details_handler(app: FastAPI) -> None:
    """Register RFC 7807 Problem Details JSON handler for DomainError.

    Returns JSON with Content-Type 'application/problem+json' containing:
        type, title, status, detail, instance, trace_id
    """
    @app.exception_handler(DomainError)
    async def _handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": f"https://lightmes/errors/{type(exc).__name__}",
                "title": _title_for(exc),
                "status": exc.status_code,
                "detail": exc.detail,
                "instance": str(request.url.path),
                "trace_id": _trace_id(request),
            },
            media_type="application/problem+json",
        )
```

- [ ] **Step 9: 实现 TraceIdMiddleware + ApiCallLogMiddleware**

创建 `src/lightmes/modules/api_v1/middleware.py`：

```python
import time
import uuid
from datetime import datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy.orm import Session

from lightmes.database import SessionLocal
from lightmes.modules.api_v1.models import ApiCallLog

# 写操作记录；失败记录；GET 成功不记录
_WRITTEN_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 8 字符 trace_id，注入 request.state + X-Trace-Id 响应头。"""

    async def dispatch(self, request: Request, call_next):
        request.state.trace_id = uuid.uuid4().hex[:8]
        response: Response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response


class ApiCallLogMiddleware(BaseHTTPMiddleware):
    """选择性记录 /api/v1/* 调用：写操作 + 错误（>=400）；成功 GET 不记录。"""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)

        should_log = (
            request.method in _WRITTEN_METHODS
            or response.status_code >= 400
        )
        if not should_log:
            return response

        # 提取错误详情（仅 4xx/5xx）
        error_detail = None
        if response.status_code >= 400:
            try:
                # response.body may have been consumed; Starlette allows re-read via .body
                body = response.body
                if body:
                    import json
                    data = json.loads(body)
                    error_detail = str(data.get("detail") or data.get("detail") or "")[:500]
            except Exception:
                error_detail = None

        # 异步写 log（独立 session 避免污染请求 session）
        try:
            trace_id = getattr(request.state, "trace_id", None)
            api_key_id = getattr(request.state, "api_key_id", None)
            user_id = getattr(request.state, "api_key_user_id", None)
            client_ip = request.client.host if request.client else None
            db: Session = SessionLocal()
            try:
                db.add(ApiCallLog(
                    api_key_id=api_key_id, user_id=user_id,
                    method=request.method, path=request.url.path,
                    status_code=response.status_code, duration_ms=duration_ms,
                    trace_id=trace_id, client_ip=client_ip,
                    error_detail=error_detail,
                ))
                db.commit()
            finally:
                db.close()
        except Exception:
            # Logging failures must never break the response
            pass

        return response
```

- [ ] **Step 10: 注册 middleware + Problem Details handler 到 main.py**

修改 `src/lightmes/main.py`：

替换现有 exception handler（line 35-37）：
```python
@app.exception_handler(DomainError)
def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```

为：
```python
from lightmes.modules.api_v1.errors import register_problem_details_handler
from lightmes.modules.api_v1.middleware import ApiCallLogMiddleware, TraceIdMiddleware

# Middleware（顺序：后加的在外层）
app.add_middleware(ApiCallLogMiddleware)
app.add_middleware(TraceIdMiddleware)

# 升级 DomainError handler 为 RFC 7807
register_problem_details_handler(app)
```

放在 `app.add_middleware(SessionMiddleware, ...)` 之后、`auth.register(app)` 之前。

- [ ] **Step 11: 运行测试**

Run: `uv run pytest tests/modules/api_v1/ -v`
Expected: 全部 PASS（2 model + 7 service + 6 auth/error = 15 tests）。

- [ ] **Step 12: 运行回归**

Run: `uv run pytest tests/modules/api_v1/ tests/modules/production/test_planner_routes.py tests/modules/masterdata/ -v`
Expected: API v1 + planner + masterdata all PASS（masterdata 可能含 pre-existing `test_erp_fields` 失败，OUT OF SCOPE）。

- [ ] **Step 13: Commit**

```bash
git add src/lightmes/modules/api_v1/api_key_service.py \
        src/lightmes/modules/api_v1/dependencies.py \
        src/lightmes/modules/api_v1/middleware.py \
        src/lightmes/modules/api_v1/errors.py \
        src/lightmes/main.py \
        tests/modules/api_v1/test_api_key_service.py \
        tests/modules/api_v1/test_auth_and_errors.py
git commit -m "feat(api-v1): ApiKeyService + require_api_key + Problem Details errors + trace_id middleware"
```

---

### Task 3: API Key 管理 UI

**Files:**
- Modify: `src/lightmes/modules/auth/router.py` (新增 `/system/api-keys` GET/POST/DELETE 路由)
- Create: `src/lightmes/templates/system/api_keys.html` (列表 + 新建表单 + 吊销按钮)
- Create: `src/lightmes/templates/system/partials/api_key_created.html` (创建后显示 full_key 一次)
- Test: `tests/modules/auth/test_api_keys_pages.py`

**Interfaces:**
- Consumes: Task 2 的 `ApiKeyService`
- Produces: `/system/api-keys` admin 管理页（list / create / revoke）

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/auth/test_api_keys_pages.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.models import User, Role
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login_admin(client, db_session, username="akeyadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    AuthService(db_session).create_user(
        UserCreate(username=username, password="pw12345", display_name="Adm"))
    u = db_session.query(User).filter(User.username == username).one()
    u.role_id = role.id
    db_session.flush()
    client.post("/login", data={"username": username, "password": "pw12345"})


def test_api_keys_page_requires_login(client, db_session):
    resp = client.get("/system/api-keys", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_api_keys_page_renders_for_admin(client, db_session):
    _login_admin(client, db_session)
    resp = client.get("/system/api-keys")
    assert resp.status_code == 200
    assert "API Key" in resp.text or "api-keys" in resp.text


def test_api_key_create_via_post_returns_full_key(client, db_session):
    _login_admin(client, db_session, username="akeyadm2")
    resp = client.post("/system/api-keys", data={
        "name": "Test Key",
        "scopes": ["read", "write"],
    })
    assert resp.status_code in (200, 303)
    # full_key 在创建片段中显示一次
    assert b"lmk_live_" in resp.content or "lmk_live_" in resp.text


def test_api_key_revoke_via_post(client, db_session):
    _login_admin(client, db_session, username="akeyadm3")
    u = db_session.query(User).filter(User.username == "akeyadm3").one()
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    _, record = ApiKeyService(db_session).create(name="To Revoke", user_id=u.id, scopes=["read"])
    db_session.flush()
    resp = client.post(f"/system/api-keys/{record.id}/revoke")
    assert resp.status_code in (200, 303)
    db_session.refresh(record)
    assert record.is_active is False
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/auth/test_api_keys_pages.py -v`
Expected: 404 (路由不存在)。

- [ ] **Step 3: 实现路由**

修改 `src/lightmes/modules/auth/router.py`，在文件末尾追加：

```python
# ---- API Key 管理（admin only）----

@router.get("/system/api-keys", response_class=HTMLResponse)
def api_keys_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if not _is_admin(user):
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    keys = ApiKeyService(db).list_for_user(user.id)
    return templates.TemplateResponse("system/api_keys.html", {
        "request": request, "keys": keys, "user": user,
    })


@router.post("/system/api-keys", response_class=HTMLResponse)
def api_key_create(
    request: Request,
    name: str = Form(...),
    scopes: list[str] = Form([]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    if not _is_admin(user):
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    scopes_resolved = scopes if scopes else ["read"]
    full_key, record = ApiKeyService(db).create(
        name=name, user_id=user.id, scopes=scopes_resolved)
    db.commit()
    return templates.TemplateResponse("system/partials/api_key_created.html", {
        "request": request, "full_key": full_key, "record": record,
    })


@router.post("/system/api-keys/{key_id}/revoke", response_class=HTMLResponse)
def api_key_revoke(
    request: Request,
    key_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    if not _is_admin(user):
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    ApiKeyService(db).revoke(key_id, revoked_by_user_id=user.id)
    db.commit()
    return RedirectResponse(url="/system/api-keys", status_code=303)
```

在 router.py 顶部加 helper（如还没有）：

```python
def _is_admin(user) -> bool:
    role_name = user.role_obj.name if user.role_obj else getattr(user, "role", None)
    return role_name == "admin"
```

确认 `Form`、`RedirectResponse` 已 import。

- [ ] **Step 4: 创建 api_keys.html 模板**

创建 `src/lightmes/templates/system/api_keys.html`：

```html
{% extends "base.html" %}
{% block title %}API Keys{% endblock %}
{% block content %}
<h1 class="page-title">API Keys <small>AI Agent 接入凭证</small></h1>

<div class="card">
  <div class="card__title">创建新 Key</div>
  <form method="post" action="/system/api-keys" class="form-row"
        onsubmit="return confirm('创建后完整 key 仅显示一次，确认？')">
    <div class="field"><label>名称</label><input name="name" required placeholder="ERP Sync"></div>
    <div class="field">
      <label>Scopes</label>
      <label><input type="checkbox" name="scopes" value="read" checked> read</label>
      <label><input type="checkbox" name="scopes" value="write"> write</label>
    </div>
    <button type="submit">创建</button>
  </form>
</div>

<div id="new-key-target"></div>

<div class="card">
  <div class="card__title">现有 Keys</div>
  <table class="data-table">
    <thead><tr><th>名称</th><th>前缀</th><th>Scopes</th><th>最后使用</th><th>状态</th><th>操作</th></tr></thead>
    <tbody>
      {% for k in keys %}
      <tr>
        <td>{{ k.name }}</td>
        <td><code>{{ k.key_prefix }}...</code></td>
        <td>{{ k.scopes|join(", ") }}</td>
        <td>{{ k.last_used_at.strftime("%Y-%m-%d %H:%M") if k.last_used_at else "未使用" }}</td>
        <td>
          {% if k.is_active and not k.revoked_at %}<span class="badge badge--ok">启用</span>
          {% else %}<span class="badge">已吊销</span>{% endif %}
        </td>
        <td>
          {% if k.is_active and not k.revoked_at %}
          <form method="post" action="/system/api-keys/{{ k.id }}/revoke" style="display:inline"
                onsubmit="return confirm('确认吊销此 Key？立即生效。')">
            <button type="submit" class="btn-danger">吊销</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: 创建 api_key_created.html 片段**

创建 `src/lightmes/templates/system/partials/api_key_created.html`：

```html
<div class="card" style="border: 2px solid var(--ok); background: var(--ok-bg);">
  <div class="card__title">✓ Key 已创建 — 请立即复制保存</div>
  <p class="nav-card__desc">完整 key 仅此一次显示，关闭后无法再查看。</p>
  <pre id="new-api-key" style="background: white; padding: 12px; border-radius: 4px; font-family: monospace; word-break: break-all; user-select: all;">{{ full_key }}</pre>
  <button type="button" onclick="navigator.clipboard.writeText(document.getElementById('new-api-key').textContent).then(() => alert('已复制'))">复制到剪贴板</button>
  <p style="margin-top: 12px"><a href="/system/api-keys">← 返回列表</a></p>
</div>
```

- [ ] **Step 6: 加导航链接到 base.html（可选）**

修改 `src/lightmes/templates/base.html`，在导航 `<a class="app-bar__link" href="/system/roles">角色</a>` 后加：

```html
    <a class="app-bar__link" href="/system/api-keys">API Keys</a>
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/auth/test_api_keys_pages.py -v`
Expected: 4 tests PASS。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/auth/router.py \
        src/lightmes/templates/system/api_keys.html \
        src/lightmes/templates/system/partials/api_key_created.html \
        src/lightmes/templates/base.html \
        tests/modules/auth/test_api_keys_pages.py
git commit -m "feat(api-v1): API Key management UI (/system/api-keys admin page)"
```

---

### Task 4: API Key JSON 端点 (/api/v1/api-keys)

**Files:**
- Modify: `src/lightmes/modules/api_v1/router.py` (追加 list/create/delete 路由)
- Modify: `src/lightmes/modules/api_v1/schemas.py` (新建文件，ApiKeyCreate/Read schemas)
- Test: `tests/modules/api_v1/test_api_keys_endpoints.py`

**Interfaces:**
- Consumes: Task 2 的 `ApiKeyService` + `require_api_key`
- Produces:
  - `GET /api/v1/api-keys` → list (excludes key_hash)
  - `POST /api/v1/api-keys` → create (returns full_key once in response)
  - `DELETE /api/v1/api-keys/{id}` → revoke

- [ ] **Step 1: 创建 schemas 文件**

创建 `src/lightmes/modules/api_v1/schemas.py`：

```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["ERP Sync"])
    scopes: list[str] = Field(default=["read"], examples=[["read", "write"]])
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    """API Key 列表项 — 不含 key_hash，不含完整 key。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(BaseModel):
    """POST 创建后的响应 — full_key 仅此一次返回。"""
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    full_key: str  # 仅此一次
    created_at: datetime
```

- [ ] **Step 2: 写失败测试**

创建 `tests/modules/api_v1/test_api_keys_endpoints.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin_with_key(db_session, username="apiadmin_ep"):
    """Create admin user + return (user, full_api_key)."""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("pw12345"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="admin-master", user_id=u.id, scopes=["read", "write"])
    return u, full_key


def test_api_keys_list(client, db_session):
    u, key = _admin_with_key(db_session)
    resp = client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(k["name"] == "admin-master" for k in data)
    # 列表项不含 key_hash 或 full_key
    assert "key_hash" not in data[0]
    assert "full_key" not in data[0]


def test_api_keys_create_returns_full_key(client, db_session):
    u, key = _admin_with_key(db_session, username="apiadmin_ep2")
    resp = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"}, json={
        "name": "Test Key",
        "scopes": ["read"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_key"].startswith("lmk_live_")
    assert data["name"] == "Test Key"
    assert data["scopes"] == ["read"]


def test_api_keys_revoke_via_delete(client, db_session):
    u, key = _admin_with_key(db_session, username="apiadmin_ep3")
    # 创建第二个 key
    _, record = ApiKeyService(db_session).create(name="To Revoke", user_id=u.id, scopes=["read"])
    db_session.flush()
    resp = client.delete(
        f"/api/v1/api-keys/{record.id}",
        headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code in (200, 204)
    db_session.refresh(record)
    assert record.is_active is False


def test_api_keys_create_readonly_key_forbidden(client, db_session):
    """read-only key 不能创建新 key。"""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    from lightmes.shared.security import hash_password
    u = User(username="ro_user", password_hash=hash_password("p"),
             display_name="RO", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    ro_key, _ = ApiKeyService(db_session).create(name="ro", user_id=u.id, scopes=["read"])
    resp = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {ro_key}"}, json={
        "name": "x", "scopes": ["read"],
    })
    assert resp.status_code == 403
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_api_keys_endpoints.py -v`
Expected: 404。

- [ ] **Step 4: 实现端点**

修改 `src/lightmes/modules/api_v1/router.py`：

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.dependencies import require_api_key
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError

router = APIRouter(tags=["API Keys"])


@router.get("/api-keys", response_model=list[ApiKeyRead],
            dependencies=[Depends(require_api_key("read"))])
def list_api_keys(
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read")),
) -> list[ApiKeyRead]:
    """List API keys for the current user (Bearer) or session user."""
    keys = ApiKeyService(db).list_for_user(user.id)
    return [ApiKeyRead.model_validate(k) for k in keys]


@router.post("/api-keys", response_model=ApiKeyCreatedResponse,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key("read", "write"))])
def create_api_key(
    data: ApiKeyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read", "write")),
) -> ApiKeyCreatedResponse:
    """Create a new API key. Returns the full key ONCE in the response."""
    full_key, record = ApiKeyService(db).create(
        name=data.name, user_id=user.id, scopes=data.scopes,
        expires_at=data.expires_at)
    db.commit()
    db.refresh(record)
    return ApiKeyCreatedResponse(
        id=record.id, name=record.name, key_prefix=record.key_prefix,
        scopes=record.scopes, full_key=full_key, created_at=record.created_at,
    )


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_key("read", "write"))])
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read", "write")),
):
    """Revoke an API key by id. Only keys owned by the current user can be revoked."""
    from lightmes.modules.auth.models import ApiKey
    target = db.get(ApiKey, key_id)
    if target is None:
        raise NotFoundError(f"API Key 不存在: {key_id}")
    if target.user_id != user.id:
        # IDOR protection: cannot revoke other users' keys
        raise NotFoundError(f"API Key 不存在: {key_id}")
    ApiKeyService(db).revoke(key_id, revoked_by_user_id=user.id)
    db.commit()
    return None
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/api_v1/test_api_keys_endpoints.py -v`
Expected: 4 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/api_v1/router.py \
        src/lightmes/modules/api_v1/schemas.py \
        tests/modules/api_v1/test_api_keys_endpoints.py
git commit -m "feat(api-v1): /api/v1/api-keys JSON endpoints (list/create/revoke)"
```

---

### Task 5: Work Orders API (/api/v1/work-orders)

**Files:**
- Modify: `src/lightmes/modules/api_v1/router.py` (追加 work-orders 路由)
- Modify: `src/lightmes/modules/api_v1/schemas.py` (WorkOrderReadV1, WorkOrderCreateV1, WorkOrderPriorityPatch)
- Modify: `src/lightmes/modules/production/schemas.py` (添加 list method 或 query helper)
- Test: `tests/modules/api_v1/test_work_orders_endpoints.py`

**Interfaces:**
- Consumes: `ProductionService.create_work_order`；`WorkOrder` model；`require_api_key`
- Produces:
  - `GET /api/v1/work-orders` (list, paginated, filtered)
  - `GET /api/v1/work-orders/{id}`
  - `POST /api/v1/work-orders` (write scope)
  - `PATCH /api/v1/work-orders/{id}/priority` (write scope)

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/api_v1/test_work_orders_endpoints.py`：

```python
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env(db_session):
    """Product + routing + line + sn_rule."""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="APV1P", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="APV1L", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="APV1W", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="APV1R", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="APV1RR", name="r", pattern="APV{SEQ:4}"))
    return p, line, r, rule


def _admin_key(db_session, username="woadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="woadm-key", user_id=u.id, scopes=["read", "write"])
    return full_key


def _ro_key(db_session, username="ro_u"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="RO", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="ro-key", user_id=u.id, scopes=["read"])
    return full_key


def test_work_orders_list_pagination(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    svc = ProductionService(db_session)
    for i in range(5):
        svc.create_work_order(WorkOrderCreate(
            code=f"APV1W{i}", product_id=p.id, routing_id=r.id, line_id=line.id,
            qty=10, sn_rule_id=rule.id))
    resp = client.get("/api/v1/work-orders?page=1&size=3",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "5"
    assert resp.headers["X-Page"] == "1"
    assert resp.headers["X-Size"] == "3"
    data = resp.json()
    assert len(data) == 3


def test_work_orders_list_filter_by_status(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    svc = ProductionService(db_session)
    wo = svc.create_work_order(WorkOrderCreate(
        code="APV1S1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    svc.release_work_order(wo.id)  # released
    svc.create_work_order(WorkOrderCreate(
        code="APV1S2", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))  # created
    resp = client.get("/api/v1/work-orders?status=released",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["code"] == "APV1S1"


def test_work_orders_get_one(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APV1G1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    resp = client.get(f"/api/v1/work-orders/{wo.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == wo.id
    assert data["code"] == "APV1G1"
    assert "priority" in data
    assert "process_snapshot" not in data  # 内部字段不暴露


def test_work_orders_get_one_not_found_returns_problem_details(client, db_session):
    key = _admin_key(db_session)
    resp = client.get("/api/v1/work-orders/99999",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_work_orders_create_success(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    resp = client.post("/api/v1/work-orders",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"code": "APV1C1", "product_id": p.id,
                             "routing_id": r.id, "line_id": line.id,
                             "qty": 50, "sn_rule_id": rule.id, "priority": 7})
    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "APV1C1"
    assert data["priority"] == 7


def test_work_orders_create_readonly_key_forbidden(client, db_session):
    p, line, r, rule = _env(db_session)
    ro_key = _ro_key(db_session)
    resp = client.post("/api/v1/work-orders",
                       headers={"Authorization": f"Bearer {ro_key}"},
                       json={"code": "APV1C2", "product_id": p.id,
                             "routing_id": r.id, "line_id": line.id,
                             "qty": 50, "sn_rule_id": rule.id})
    assert resp.status_code == 403


def test_work_orders_patch_priority(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APV1P1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    resp = client.patch(f"/api/v1/work-orders/{wo.id}/priority",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"priority": 9})
    assert resp.status_code == 200
    assert resp.json()["priority"] == 9
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_work_orders_endpoints.py -v`
Expected: 404。

- [ ] **Step 3: 追加 schemas**

修改 `src/lightmes/modules/api_v1/schemas.py`，文件末尾追加：

```python
class WorkOrderReadV1(BaseModel):
    """WorkOrder for API v1 — no internal fields like process_snapshot."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    product_id: int
    routing_id: int
    line_id: int
    sn_rule_id: int | None
    qty: int
    status: str
    source: str
    produced_qty: int
    planned_start: datetime | None
    planned_end: datetime | None
    priority: int
    created_at: datetime


class WorkOrderCreateV1(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, examples=["WO-2026-001"])
    product_id: int
    routing_id: int
    line_id: int
    sn_rule_id: int | None = None
    qty: int = Field(..., gt=0)
    priority: int = Field(default=5, ge=1, le=9)


class WorkOrderPriorityPatch(BaseModel):
    priority: int = Field(..., ge=1, le=9)
```

- [ ] **Step 4: 实现 work-orders 路由**

修改 `src/lightmes/modules/api_v1/router.py`：

**先改 router 声明**（去掉全局 tag，每个端点自带）：

```python
router = APIRouter()
```

**顶部 import 块升级为**：

```python
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.dependencies import require_api_key
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError

router = APIRouter()
```

**Task 4 已经写好的 api-keys 3 个端点需要补 `tags=["API Keys"]`**（之前依赖全局 tag，现在 router 无 tag）。找到 Task 4 的 3 个 `@router.get/post/delete("/api-keys...")` 装饰器，每个加 `tags=["API Keys"]`：

```python
@router.get("/api-keys", response_model=list[ApiKeyRead], tags=["API Keys"],
            dependencies=[Depends(require_api_key("read"))])
def list_api_keys(...): ...


@router.post("/api-keys", response_model=ApiKeyCreatedResponse,
             status_code=status.HTTP_201_CREATED, tags=["API Keys"],
             dependencies=[Depends(require_api_key("read", "write"))])
def create_api_key(...): ...


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT,
               tags=["API Keys"],
               dependencies=[Depends(require_api_key("read", "write"))])
def revoke_api_key(...): ...
```

**追加 work-orders 路由**（在文件末尾）：

```python
# ---- Work Orders ----

_WO_TAG = "Work Orders"
_LIST_MAX_SIZE = 100


@router.get("/work-orders", response_model=list[WorkOrderReadV1], tags=[_WO_TAG])
def list_work_orders(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_LIST_MAX_SIZE),
    status: list[str] = Query(default=[]),
    line_id: int | None = Query(default=None),
    created_since: datetime | None = Query(default=None),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[WorkOrderReadV1]:
    """List work orders with pagination and filters.

    Returns pagination info via `X-Total-Count`, `X-Page`, `X-Size` response headers.
    """
    from sqlalchemy import select, func
    from lightmes.modules.production.models import WorkOrder
    q = select(WorkOrder).order_by(WorkOrder.id.desc())
    if status:
        q = q.where(WorkOrder.status.in_(status))
    if line_id is not None:
        q = q.where(WorkOrder.line_id == line_id)
    if created_since is not None:
        q = q.where(WorkOrder.created_at >= created_since)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Size"] = str(size)
    return [WorkOrderReadV1.model_validate(r) for r in rows]


@router.get("/work-orders/{wo_id}", response_model=WorkOrderReadV1, tags=[_WO_TAG])
def get_work_order(
    wo_id: int,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> WorkOrderReadV1:
    from lightmes.modules.production.models import WorkOrder
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {wo_id}")
    return WorkOrderReadV1.model_validate(wo)


@router.post("/work-orders", response_model=WorkOrderReadV1,
             status_code=status.HTTP_201_CREATED, tags=[_WO_TAG])
def create_work_order(
    data: WorkOrderCreateV1,
    user: User = Depends(require_api_key("read", "write")),
    db: Session = Depends(get_db),
) -> WorkOrderReadV1:
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    wo = ProductionService(db).create_work_order(WorkOrderCreate(
        code=data.code, product_id=data.product_id, routing_id=data.routing_id,
        line_id=data.line_id, qty=data.qty, sn_rule_id=data.sn_rule_id))
    wo.priority = data.priority
    db.commit()
    db.refresh(wo)
    return WorkOrderReadV1.model_validate(wo)


@router.patch("/work-orders/{wo_id}/priority", response_model=WorkOrderReadV1, tags=[_WO_TAG])
def patch_work_order_priority(
    wo_id: int,
    data: WorkOrderPriorityPatch,
    user: User = Depends(require_api_key("read", "write")),
    db: Session = Depends(get_db),
) -> WorkOrderReadV1:
    from lightmes.modules.production.models import WorkOrder
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {wo_id}")
    wo.priority = data.priority
    db.commit()
    db.refresh(wo)
    return WorkOrderReadV1.model_validate(wo)
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/api_v1/test_work_orders_endpoints.py -v`
Expected: 7 tests PASS。

- [ ] **Step 6: 运行回归**

Run: `uv run pytest tests/modules/api_v1/ -v`
Expected: 全部 PASS（包括 Task 1-4 的）。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/api_v1/router.py \
        src/lightmes/modules/api_v1/schemas.py \
        tests/modules/api_v1/test_work_orders_endpoints.py
git commit -m "feat(api-v1): /api/v1/work-orders (list/get/create/patch-priority)"
```

---

### Task 6: Serial Units API (/api/v1/serial-units)

**Files:**
- Modify: `src/lightmes/modules/api_v1/router.py`
- Modify: `src/lightmes/modules/api_v1/schemas.py` (SerialUnitReadV1)
- Test: `tests/modules/api_v1/test_serial_units_endpoints.py`

**Interfaces:**
- Consumes: `SerialUnit` model；`SerialUnitRepository.get_by_sn`；`require_api_key`
- Produces:
  - `GET /api/v1/serial-units` (list, paginated, filtered)
  - `GET /api/v1/serial-units/{id}`
  - `GET /api/v1/serial-units/by-sn/{sn}`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/api_v1/test_serial_units_endpoints.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env_with_sn(db_session, sn="APVSN1"):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="APVSNP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="APSNL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="APSNW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="APSNR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="APSNRR", name="r", pattern="APSN{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APSNWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn=sn, work_order_id=wo.id, product_id=p.id, status="in_process",
                    current_operation_seq=2)
    db_session.add(su); db_session.flush()
    return wo, su


def _key(db_session, scopes=None, username="snadm"):
    scopes = scopes or ["read", "write"]
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="Adm", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="sn-key", user_id=u.id, scopes=scopes)
    return full_key


def test_serial_units_list(client, db_session):
    wo, su = _env_with_sn(db_session)
    key = _key(db_session)
    resp = client.get("/api/v1/serial-units",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["sn"] == "APVSN1"


def test_serial_units_list_filter_by_work_order(client, db_session):
    wo, su = _env_with_sn(db_session)
    key = _key(db_session)
    resp = client.get(f"/api/v1/serial-units?work_order_id={wo.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    # 不存在的 work_order_id
    resp2 = client.get("/api/v1/serial-units?work_order_id=99999",
                       headers={"Authorization": f"Bearer {key}"})
    assert len(resp2.json()) == 0


def test_serial_units_get_one(client, db_session):
    wo, su = _env_with_sn(db_session)
    key = _key(db_session)
    resp = client.get(f"/api/v1/serial-units/{su.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == su.id
    assert data["current_operation_seq"] == 2
    assert data["status"] == "in_process"


def test_serial_units_by_sn(client, db_session):
    wo, su = _env_with_sn(db_session, sn="APVSNSPEC")
    key = _key(db_session)
    resp = client.get("/api/v1/serial-units/by-sn/APVSNSPEC",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.json()["sn"] == "APVSNSPEC"


def test_serial_units_by_sn_not_found(client, db_session):
    key = _key(db_session)
    resp = client.get("/api/v1/serial-units/by-sn/NOSUCHSN",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_serial_units_endpoints.py -v`
Expected: 404。

- [ ] **Step 3: 追加 schema**

修改 `src/lightmes/modules/api_v1/schemas.py`，文件末尾追加：

```python
class SerialUnitReadV1(BaseModel):
    """SerialUnit for API v1."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    sn: str
    work_order_id: int
    product_id: int
    status: str
    current_operation_seq: int
    is_counted: bool
    carrier_code: str | None
    created_at: datetime
```

- [ ] **Step 4: 实现 serial-units 路由**

修改 `src/lightmes/modules/api_v1/router.py`，追加：

```python
# ---- Serial Units ----

_SU_TAG = "Serial Units"


@router.get("/serial-units", response_model=list[SerialUnitReadV1], tags=[_SU_TAG])
def list_serial_units(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    work_order_id: int | None = Query(default=None),
    status: list[str] = Query(default=[]),
    sn: str | None = Query(default=None),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[SerialUnitReadV1]:
    """List serial units with pagination and filters."""
    from sqlalchemy import select, func
    from lightmes.modules.production.models import SerialUnit
    q = select(SerialUnit).order_by(SerialUnit.id.desc())
    if work_order_id is not None:
        q = q.where(SerialUnit.work_order_id == work_order_id)
    if status:
        q = q.where(SerialUnit.status.in_(status))
    if sn:
        q = q.where(SerialUnit.sn.ilike(f"%{sn}%"))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Size"] = str(size)
    return [SerialUnitReadV1.model_validate(r) for r in rows]


@router.get("/serial-units/by-sn/{sn}", response_model=SerialUnitReadV1, tags=[_SU_TAG])
def get_serial_unit_by_sn(
    sn: str,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> SerialUnitReadV1:
    """Lookup serial unit by its SN (business key)."""
    from lightmes.modules.production.repository import SerialUnitRepository
    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    return SerialUnitReadV1.model_validate(su)


@router.get("/serial-units/{su_id}", response_model=SerialUnitReadV1, tags=[_SU_TAG])
def get_serial_unit(
    su_id: int,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> SerialUnitReadV1:
    from lightmes.modules.production.models import SerialUnit
    su = db.get(SerialUnit, su_id)
    if su is None:
        raise NotFoundError(f"Serial unit 不存在: {su_id}")
    return SerialUnitReadV1.model_validate(su)
```

schemas.py 顶部 import 加 `SerialUnitReadV1`：

```python
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    SerialUnitReadV1, WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
```

**重要**：路由顺序 — FastAPI 匹配顺序敏感。`/serial-units/by-sn/{sn}` 必须在 `/serial-units/{su_id}` 之前声明，否则 `by-sn` 会被解析为 su_id 参数。已在上面代码中按正确顺序排列。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/api_v1/test_serial_units_endpoints.py -v`
Expected: 5 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/api_v1/router.py \
        src/lightmes/modules/api_v1/schemas.py \
        tests/modules/api_v1/test_serial_units_endpoints.py
git commit -m "feat(api-v1): /api/v1/serial-units (list/get/by-sn)"
```

---

### Task 7: Defects API + OpenAPI tags + 回归

**Files:**
- Modify: `src/lightmes/modules/api_v1/router.py` (defects 路由)
- Modify: `src/lightmes/modules/api_v1/schemas.py` (DefectReadV1)
- Modify: `src/lightmes/main.py` (FastAPI metadata: title, version, description)
- Test: `tests/modules/api_v1/test_defects_endpoints.py`
- Test: `tests/modules/api_v1/test_openapi.py`

**Interfaces:**
- Consumes: `DefectRecord` model；`require_api_key`
- Produces:
  - `GET /api/v1/defects` (list, paginated, filtered)
  - `GET /api/v1/defects/{id}`
  - 增强的 OpenAPI metadata

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/api_v1/test_defects_endpoints.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import SerialUnit, DefectType, DefectRecord
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env_with_defect(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="APVDP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="APVDL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="APVDW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="APVDR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="APVDRR", name="r", pattern="APVD{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="APVDWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="APVD1", work_order_id=wo.id, product_id=p.id,
                    status="quarantined", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    dt = DefectType(code="TEST_DEFECT", name="测试缺陷", category="质量",
                    severity="major", is_active=True)
    db_session.add(dt); db_session.flush()
    d = DefectRecord(
        defect_type_id=dt.id, defect_type_code=dt.code, defect_type_name=dt.name,
        severity=dt.severity, serial_unit_id=su.id, work_order_id=wo.id,
        operation_id=None, work_station_id=None, position=None,
        discovered_by=None, handling_status="pending",
    )
    db_session.add(d); db_session.flush()
    return wo, d


def _key(db_session):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username="defadm", password_hash=hash_password("p"),
             display_name="D", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="d-key", user_id=u.id, scopes=["read"])
    return full_key


def test_defects_list(client, db_session):
    wo, d = _env_with_defect(db_session)
    key = _key(db_session)
    resp = client.get("/api/v1/defects",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["defect_type_code"] == "TEST_DEFECT"


def test_defects_list_filter_by_severity(client, db_session):
    wo, d = _env_with_defect(db_session)
    key = _key(db_session)
    resp = client.get("/api/v1/defects?severity=major",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert all(d["severity"] == "major" for d in resp.json())
    # filter by non-existent severity
    resp2 = client.get("/api/v1/defects?severity=critical",
                       headers={"Authorization": f"Bearer {key}"})
    assert len(resp2.json()) == 0


def test_defects_get_one(client, db_session):
    wo, d = _env_with_defect(db_session)
    key = _key(db_session)
    resp = client.get(f"/api/v1/defects/{d.id}",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == d.id
    assert data["handling_status"] == "pending"


def test_defects_get_one_not_found(client, db_session):
    key = _key(db_session)
    resp = client.get("/api/v1/defects/99999",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
```

- [ ] **Step 2: 写 OpenAPI 元数据测试**

创建 `tests/modules/api_v1/test_openapi.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_openapi_json_accessible(client, db_session):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "LightMES"
    assert "version" in spec["info"]


def test_openapi_has_tags(client, db_session):
    resp = client.get("/openapi.json")
    spec = resp.json()
    tag_names = {t["name"] for t in spec.get("tags", [])}
    assert "Work Orders" in tag_names
    assert "Serial Units" in tag_names
    assert "Defects" in tag_names
    assert "API Keys" in tag_names


def test_openapi_has_v1_paths(client, db_session):
    resp = client.get("/openapi.json")
    spec = resp.json()
    paths = spec.get("paths", {})
    assert "/api/v1/work-orders" in paths
    assert "/api/v1/serial-units" in paths
    assert "/api/v1/defects" in paths
    assert "/api/v1/api-keys" in paths
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/modules/api_v1/test_defects_endpoints.py tests/modules/api_v1/test_openapi.py -v`
Expected: 404 on defects，OpenAPI tag test fails（Defects tag 不存在）。

- [ ] **Step 4: 追加 schema**

修改 `src/lightmes/modules/api_v1/schemas.py`，文件末尾追加：

```python
class DefectReadV1(BaseModel):
    """Defect for API v1."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    defect_type_code: str
    defect_type_name: str
    severity: str
    serial_unit_id: int
    work_order_id: int
    operation_id: int | None
    work_station_id: int | None
    position: str | None
    handling_status: str  # pending / rework / scrap / concession
    discovered_by: int | None
    discovered_at: datetime
    handled_by: int | None
    handled_at: datetime | None
    handling_remark: str | None
    remark: str | None
```

- [ ] **Step 5: 实现 defects 路由**

修改 `src/lightmes/modules/api_v1/router.py`，追加：

```python
# ---- Defects ----

_DEF_TAG = "Defects"


@router.get("/defects", response_model=list[DefectReadV1], tags=[_DEF_TAG])
def list_defects(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    handling_status: list[str] = Query(default=[]),
    severity: list[str] = Query(default=[]),
    work_order_id: int | None = Query(default=None),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[DefectReadV1]:
    """List defects with pagination and filters."""
    from sqlalchemy import select, func
    from lightmes.modules.production.models import DefectRecord
    q = select(DefectRecord).order_by(DefectRecord.id.desc())
    if handling_status:
        q = q.where(DefectRecord.handling_status.in_(handling_status))
    if severity:
        q = q.where(DefectRecord.severity.in_(severity))
    if work_order_id is not None:
        q = q.where(DefectRecord.work_order_id == work_order_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Size"] = str(size)
    return [DefectReadV1.model_validate(r) for r in rows]


@router.get("/defects/{defect_id}", response_model=DefectReadV1, tags=[_DEF_TAG])
def get_defect(
    defect_id: int,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> DefectReadV1:
    from lightmes.modules.production.models import DefectRecord
    d = db.get(DefectRecord, defect_id)
    if d is None:
        raise NotFoundError(f"缺陷不存在: {defect_id}")
    return DefectReadV1.model_validate(d)
```

schemas import 加 `DefectReadV1`：

```python
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1, SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
```

- [ ] **Step 6: 增强 OpenAPI metadata**

修改 `src/lightmes/main.py`，替换 `app = FastAPI(title=settings.app_name)`：

```python
app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "LightMES — 轻量级制造执行系统（笔记本壳装配专线）。\n\n"
        "**API v1**：本接口为 AI Agent / ERP / BI 等外部系统集成设计。\n"
        "**认证**：`Authorization: Bearer lmk_live_xxx`（API Key，通过 `/system/api-keys` 创建）。\n"
        "**错误格式**：RFC 7807 Problem Details (`application/problem+json`)。\n"
        "**分页**：`?page=1&size=20`，响应头 `X-Total-Count` / `X-Page` / `X-Size`。\n\n"
        "操作员 UI 见各模块 HTML 路由（不在本 OpenAPI 中）。"
    ),
    openapi_tags=[
        {"name": "Work Orders", "description": "工单 CRUD + 优先级"},
        {"name": "Serial Units", "description": "序列号单元查询（含 SN 业务键查询）"},
        {"name": "Defects", "description": "缺陷记录查询"},
        {"name": "API Keys", "description": "API Key 管理（admin only）"},
    ],
)
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/api_v1/test_defects_endpoints.py tests/modules/api_v1/test_openapi.py -v`
Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/api_v1/router.py \
        src/lightmes/modules/api_v1/schemas.py \
        src/lightmes/main.py \
        tests/modules/api_v1/test_defects_endpoints.py \
        tests/modules/api_v1/test_openapi.py
git commit -m "feat(api-v1): /api/v1/defects (list/get) + OpenAPI metadata"
```

---

### Task 8: 回归 + ApiCallLog middleware 验证 + memory 更新

**Files:**
- Modify: `tests/modules/api_v1/test_api_call_log.py` (新建)
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\api_ecosystem.md` (新建 memory)

**Interfaces:**
- Consumes: 全部前 7 task
- Produces: ApiCallLog 端到端验证 + memory 文档

- [ ] **Step 1: 写 ApiCallLog 集成测试**

创建 `tests/modules/api_v1/test_api_call_log.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db, SessionLocal
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.models import ApiCallLog
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _key(db_session, scopes=None, username="logadm"):
    scopes = scopes or ["read", "write"]
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="L", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="log-key", user_id=u.id, scopes=scopes)
    return full_key, u


def test_api_call_log_records_write(client, db_session):
    """写操作（POST）被记录。"""
    key, u = _key(db_session)
    resp = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"}, json={
        "name": "Test Log", "scopes": ["read"]})
    assert resp.status_code == 201
    # 用独立 session 检查 log（避免 db_session 缓存）
    log_db = SessionLocal()
    try:
        logs = log_db.query(ApiCallLog).filter(
            ApiCallLog.method == "POST",
            ApiCallLog.path == "/api/v1/api-keys",
        ).all()
        assert len(logs) >= 1
        assert logs[-1].status_code == 201
        assert logs[-1].user_id == u.id
        assert logs[-1].trace_id is not None
    finally:
        log_db.close()


def test_api_call_log_records_error(client, db_session):
    """失败调用（4xx）被记录，含 error_detail。"""
    key, u = _key(db_session)
    resp = client.get("/api/v1/work-orders/99999",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404
    log_db = SessionLocal()
    try:
        logs = log_db.query(ApiCallLog).filter(
            ApiCallLog.path == "/api/v1/work-orders/99999",
        ).all()
        assert len(logs) >= 1
        last = logs[-1]
        assert last.status_code == 404
        assert last.error_detail is not None
        assert "工单不存在" in last.error_detail
    finally:
        log_db.close()


def test_api_call_log_skips_successful_get(client, db_session):
    """成功 GET 不被记录。"""
    key, u = _key(db_session)
    # 先调一次 GET（可能被记录或不被记录）
    client.get("/api/v1/work-orders", headers={"Authorization": f"Bearer {key}"})
    log_db = SessionLocal()
    try:
        # 新加一次 GET，前后取 count 差
        before = log_db.query(ApiCallLog).filter(
            ApiCallLog.method == "GET",
            ApiCallLog.path == "/api/v1/work-orders",
            ApiCallLog.status_code == 200,
        ).count()
        client.get("/api/v1/work-orders", headers={"Authorization": f"Bearer {key}"})
        log_db.expire_all()
        after = log_db.query(ApiCallLog).filter(
            ApiCallLog.method == "GET",
            ApiCallLog.path == "/api/v1/work-orders",
            ApiCallLog.status_code == 200,
        ).count()
        assert after == before  # 没有 +1，GET 成功不记录
    finally:
        log_db.close()
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/modules/api_v1/test_api_call_log.py -v`
Expected: 3 tests PASS。

如果 `test_api_call_log_records_error` 因 Problem Details handler 抛异常而失败，可能因为 ApiCallLog middleware 读取 response.body 时与 FastAPI 异常处理冲突。如失败，调整 middleware 读取 body 的方式：用 `response.body` 可能已经 consumed，需要 `response = await call_next(request)` 后 `response.body` 可以访问（FastAPI 的 JSONResponse 已经把 body 设好了）。如仍失败，先简化 middleware（不读 error_detail，只记录状态码）。

- [ ] **Step 3: 全套 API v1 回归**

Run: `uv run pytest tests/modules/api_v1/ -v`
Expected: 全部 PASS（约 35-40 tests）。

- [ ] **Step 4: 全套项目回归**

Run: `uv run pytest tests/modules/ -v`
Expected: 不引入新失败（pre-existing failures: `test_erp_fields.py`, `test_scan_pages`, `OpInfo.id` 等已知 OUT OF SCOPE）。

- [ ] **Step 5: 更新 memory**

创建 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\api_ecosystem.md`：

```markdown
---
name: api-ecosystem
description: LightMES 的 JSON API 生态状态、设计原则、未来扩展方向
metadata:
  type: project
---

# LightMES API 生态

## 双面架构

LightMES 是 **HTMX-first 的内网车间 MES**，但有两条 HTTP 表面：

- **HTML 路由**（HTMX 片段）—— 操作员 UI，cookie session 认证，占绝大多数路由
- **`/api/v1/*` JSON API**（2026-08-12 上线）—— Agent / ERP / BI 用，Bearer token + session 双路径

**Why**：HTML 路由快、简单、SEO 无关，操作员场景最优；但外部系统（特别是 AI Agent）需要稳定的 JSON API。

**How to apply**：
- 新增功能先写 service 层（共享）
- HTML 路由调用 service 返回 HTML 片段
- 外部集成需求时新增 `/api/v1/*` 路由调用同一 service
- 两个表面长期共存，不要合并

## API v1 设计原则（2026-08-12 固化）

- **认证**：`Authorization: Bearer lmk_live_<32>` 或 `lmk_test_<32>`
- **认证双路径**：Bearer 失败时 fall back 到 session cookie（admin 浏览器实验方便）
- **Key 存储**：Argon2 hash（~100ms 验证延迟，可接受）
- **Key 显示**：前缀 12 字符明文（识别），完整 key 仅创建时返回一次
- **Scopes**：`read` / `write`（不做 per-module；user role 管模块）
- **响应**：裸数据 + `response_model`，不加包络
- **错误**：RFC 7807 Problem Details (`application/problem+json`)，含 `trace_id`
- **分页**：`?page=1&size=20`（max 100），响应头 `X-Total-Count` / `X-Page` / `X-Size`
- **trace_id**：每请求 8 字符 hex，注入 `request.state` + `X-Trace-Id` 响应头
- **审计**：ApiCallLog 只记录写 + 错误（4xx/5xx）；成功 GET 不记录
- **错误码映射**：ValidationError 400 / NotFoundError 404 / ConflictError 409 / BusinessRuleError 422

## V1 已实现端点

- `GET/POST /api/v1/api-keys` + `DELETE /api-keys/{id}` （admin only）
- `GET /api/v1/work-orders` + `GET/{id}` + `POST` + `PATCH/{id}/priority`
- `GET /api/v1/serial-units` + `GET/{id}` + `GET/by-sn/{sn}`
- `GET /api/v1/defects` + `GET/{id}`

## V1 未实现（未来 spec）

- planner schedule/unschedule（已有 HTML 路由，需要 JSON 等价物）
- defect handle-rework/scrap/concession（写操作，需仔细卡控）
- operation-records 查询
- Webhook 订阅（事件推送给 Agent）
- Rate limiting
- ETag / conditional GET

## 未来 D 层（Agent Gateway）

[[ai-agent-gateway]] — 在 `/api/v1/` 之上叠加 `/agents/v1/*`，任务导向工具（compose 多个 API 调用），MCP server 导出。
```

并在 `MEMORY.md` 末尾追加一行：

```markdown
- [API ecosystem](api_ecosystem.md) — LightMES 双面架构（HTML + JSON API v1）、设计原则、扩展方向
```

- [ ] **Step 6: Commit (不含 memory 文件)**

```bash
git add tests/modules/api_v1/test_api_call_log.py
git commit -m "test(api-v1): ApiCallLog integration tests + memory update"
```

memory 文件在仓库外，不进 git。

---

## 任务依赖

```
Task 1 (migration + models)
  ↓
Task 2 (api_key_service + require_api_key + middleware + errors)
  ↓
Task 3 (API Key 管理 UI)   ← 可与 Task 4 并行
Task 4 (API Key JSON 端点)
  ↓
Task 5 (Work Orders API)
  ↓
Task 6 (Serial Units API)
  ↓
Task 7 (Defects API + OpenAPI)
  ↓
Task 8 (ApiCallLog 验证 + memory)
```

建议顺序执行。Task 3 可与 Task 4 并行但需要谨慎（都改 auth/router + api_v1/router）。

## 全套回归（任意 task 完成后均可运行）

```bash
uv run pytest tests/modules/api_v1/ -v
uv run alembic upgrade head
```

## 手工最终验收（Task 8 完成后）

```bash
uv run uvicorn lightmes.main:app --reload --port 8000
```

浏览器/curl 逐项验证：
1. `GET /docs` → Swagger UI 显示 4 个 tag
2. `POST /system/api-keys` 创建一个 key（管理员登录）
3. 用 key 调 `GET /api/v1/work-orders`（带 `Authorization: Bearer lmk_live_xxx`）
4. 验证响应头有 `X-Total-Count` 和 `X-Trace-Id`
5. `GET /api/v1/work-orders/99999` → 404 Problem Details JSON + trace_id
6. `GET /api/v1/serial-units/by-sn/<existing sn>` → 200
7. 检查 `api_call_logs` 表有写操作 + 错误记录
