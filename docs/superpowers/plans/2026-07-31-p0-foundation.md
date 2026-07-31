# P0 工程地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 LightMES 模块化单体的工程地基，并以"认证/登录"作为第一个端到端竖切贯通全栈（DB → 迁移 → repository → service → API → HTMX 页面 → 测试）。

**Architecture:** Python + FastAPI 模块化单体。单一部署单元，内部按业务模块分目录（P0 只落地 `auth` 模块 + 共享内核）。数据层 SQLAlchemy 2.0（同步）+ Alembic 迁移。前端服务端渲染（Jinja2 + HTMX）。所有依赖（PostgreSQL+TimescaleDB、Mosquitto）用 Docker Compose 起。库内事件总线在 P0 建骨架，P1 起真正使用。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2, HTMX, PostgreSQL 16 + TimescaleDB, Mosquitto (MQTT), pytest, uv, Docker Compose, GitHub Actions。

## Global Constraints

- Python 版本：**3.12**（pyproject 里 `requires-python = ">=3.12"`）
- 依赖管理：**uv**（`uv add` 装依赖，`uv run` 跑命令，`uv.lock` 入库）
- ORM：**SQLAlchemy 2.0 风格**（`Mapped[]` + `mapped_column()`，`DeclarativeBase`），同步引擎（psycopg）
- 迁移：**Alembic**，所有模型变更必须生成迁移，不允许 `create_all` 上生产
- 数据库：**PostgreSQL + TimescaleDB 扩展**，单库
- 模块边界：模块只暴露 `service` 层；跨模块不直接引用他模块的 `models`/`repository`
- 校验：入参校验在 API 边界（Pydantic schema）；内部信任
- 测试：**集成测试用真实 PostgreSQL**（Docker 起测试库），不 mock 数据库
- 提交粒度：每个 Task 末尾 commit；commit message 用 `feat:`/`test:`/`chore:`/`docs:` 前缀
- Shell：Windows 上用 bash 语法（`/dev/null` 而非 `NUL`，正斜杠路径）

---

## File Structure

P0 结束时的目录（仅列 P0 涉及文件）：

```
LightMES/
├── pyproject.toml              # uv 项目配置 + 依赖
├── uv.lock                     # 锁文件
├── docker-compose.yml          # postgres(timescale) + mosquitto + app
├── Dockerfile                  # app 镜像
├── .env.example                # 配置样例
├── .gitignore
├── alembic.ini                 # alembic 配置
├── .github/workflows/ci.yml    # CI：lint + 测试
├── src/lightmes/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app 装配、路由挂载、生命周期
│   ├── config.py               # Pydantic Settings，读环境变量
│   ├── database.py             # Engine / Session / get_db 依赖
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── base.py             # DeclarativeBase + TimestampMixin
│   │   ├── events.py           # 库内事件总线骨架
│   │   └── security.py         # 密码哈希、会话辅助
│   ├── modules/
│   │   ├── __init__.py
│   │   └── auth/
│   │       ├── __init__.py
│   │       ├── models.py       # User ORM 模型
│   │       ├── schemas.py      # Pydantic 请求/响应模型
│   │       ├── repository.py   # User 数据访问
│   │       ├── service.py      # 认证业务逻辑（模块公开接口）
│   │       └── router.py       # /api/auth + /login 页面路由
│   ├── templates/
│   │   ├── base.html
│   │   └── login.html
│   └── migrations/
│       ├── env.py
│       ├── script.py.mako
│       └── versions/           # 迁移脚本
└── tests/
    ├── __init__.py
    ├── conftest.py             # DB fixture、TestClient fixture
    ├── test_health.py          # 健康检查端到端
    └── modules/auth/
        ├── __init__.py
        ├── test_security.py    # 密码哈希单测
        ├── test_repository.py  # User repo 集成测试
        ├── test_service.py     # 认证 service 集成测试
        └── test_router.py      # 登录 API/页面端到端
```

---

### Task 1: 项目脚手架 + 健康检查端到端竖切

建立 uv 项目、FastAPI app、配置、一个 `/health` 端点，用 TestClient 端到端跑通。这是"骨架是否活着"的最小验证，不依赖数据库。

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`
- Create: `src/lightmes/__init__.py`, `src/lightmes/config.py`, `src/lightmes/main.py`
- Test: `tests/__init__.py`, `tests/test_health.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces:
  - `lightmes.config.Settings`（Pydantic Settings 类，属性：`database_url: str`, `mqtt_url: str`, `secret_key: str`, `app_name: str = "LightMES"`）
  - `lightmes.config.get_settings() -> Settings`（`@lru_cache` 单例）
  - `lightmes.main.app`（FastAPI 实例）
  - `GET /health` → `{"status": "ok", "app": "LightMES"}`

- [ ] **Step 1: 初始化 uv 项目并加依赖**

Run:
```bash
cd "C:/Users/zhaocao/Documents/GitHub/LightMES"
uv init --package --name lightmes --python 3.12 .
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings jinja2 python-multipart
uv add --dev pytest httpx
```
Expected: 生成 `pyproject.toml`、`uv.lock`、`src/lightmes/`；命令均成功退出。

- [ ] **Step 2: 写 .gitignore 和 .env.example**

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.env
.pytest_cache/
*.egg-info/
```

`.env.example`:
```
DATABASE_URL=postgresql+psycopg://mes:mes@localhost:5432/lightmes
MQTT_URL=mqtt://localhost:1883
SECRET_KEY=change-me-in-prod
```

- [ ] **Step 3: 写配置模块**

`src/lightmes/config.py`:
```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "LightMES"
    database_url: str = "postgresql+psycopg://mes:mes@localhost:5432/lightmes"
    mqtt_url: str = "mqtt://localhost:1883"
    secret_key: str = "change-me-in-prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: 写失败测试**

`tests/__init__.py`: 空文件。

`tests/test_health.py`:
```python
from fastapi.testclient import TestClient
from lightmes.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "app": "LightMES"}
```

- [ ] **Step 5: 运行测试确认失败**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL —— `ModuleNotFoundError` 或 `ImportError`（`lightmes.main` / `app` 尚不存在）。

- [ ] **Step 6: 写最小 app 实现**

`src/lightmes/main.py`:
```python
from fastapi import FastAPI
from lightmes.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS（1 passed）。

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example src/lightmes tests
git commit -m "feat: scaffold FastAPI app with health check vertical slice"
```

---

### Task 2: Docker Compose 基础设施（PostgreSQL+TimescaleDB、Mosquitto）

起本地依赖服务。用官方 `timescale/timescaledb` 镜像（自带 PG + TimescaleDB 扩展）。验证方式：启动后能连上 PG 并成功 `CREATE EXTENSION timescaledb`。

**Files:**
- Create: `docker-compose.yml`, `Dockerfile`
- Create: `docker/mosquitto/mosquitto.conf`

**Interfaces:**
- Consumes: `.env.example` 中的 `DATABASE_URL`/`MQTT_URL` 约定
- Produces: 可用的本地服务 —— PG 在 `localhost:5432`（db=`lightmes`, user=`mes`, pwd=`mes`），Mosquitto 在 `localhost:1883`；`timescaledb` 扩展可创建

- [ ] **Step 1: 写 Mosquitto 配置**

`docker/mosquitto/mosquitto.conf`:
```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
```

- [ ] **Step 2: 写 Dockerfile（app 镜像）**

`Dockerfile`（两阶段：先装依赖缓存层，再拷源码+README 装项目，避免 uv_build 构建时找不到源码/README）:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "lightmes.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: 写 docker-compose.yml**

`docker-compose.yml`:
```yaml
services:
  db:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_USER: mes
      POSTGRES_PASSWORD: mes
      POSTGRES_DB: lightmes
    ports:
      - "5432:5432"
    volumes:
      - db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mes -d lightmes"]
      interval: 5s
      timeout: 3s
      retries: 10

  mqtt:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./docker/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf
      - mqtt_data:/mosquitto/data

  app:
    build: .
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+psycopg://mes:mes@db:5432/lightmes
      MQTT_URL: mqtt://mqtt:1883
      SECRET_KEY: change-me-in-prod
    ports:
      - "8000:8000"

volumes:
  db_data:
  mqtt_data:
```

- [ ] **Step 4: 启动依赖服务**

Run: `docker compose up -d db mqtt`
Expected: 两个容器启动；`docker compose ps` 显示 `db` 为 healthy。

- [ ] **Step 5: 验证 TimescaleDB 扩展可用**

Run:
```bash
docker compose exec db psql -U mes -d lightmes -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT extname FROM pg_extension WHERE extname='timescaledb';"
```
Expected: 输出含 `timescaledb` 一行（扩展创建/已存在）。

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml Dockerfile docker/mosquitto/mosquitto.conf
git commit -m "chore: add docker-compose for postgres+timescaledb and mosquitto"
```

---

### Task 3: 数据库层 + 共享内核 Base

建立 SQLAlchemy 引擎、Session 工厂、FastAPI `get_db` 依赖，以及所有模型的公共基类（含 `created_at`/`updated_at`）。

**Files:**
- Create: `src/lightmes/shared/__init__.py`, `src/lightmes/shared/base.py`
- Create: `src/lightmes/database.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: `lightmes.config.get_settings`
- Produces:
  - `lightmes.shared.base.Base`（SQLAlchemy `DeclarativeBase` 子类）
  - `lightmes.shared.base.TimestampMixin`（提供 `created_at: Mapped[datetime]`, `updated_at: Mapped[datetime]`，均 server_default/onupdate now()）
  - `lightmes.database.engine`（SQLAlchemy Engine）
  - `lightmes.database.SessionLocal`（sessionmaker）
  - `lightmes.database.get_db() -> Iterator[Session]`（FastAPI 依赖，yield 一个 session，结束时关闭）

- [ ] **Step 1: 加数据库依赖**

Run: `uv add sqlalchemy "psycopg[binary]"`
Expected: 成功，写入 pyproject/uv.lock。

- [ ] **Step 2: 写共享 Base 与 Mixin**

`src/lightmes/shared/__init__.py`: 空文件。

`src/lightmes/shared/base.py`:
```python
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 3: 写数据库模块**

`src/lightmes/database.py`:
```python
from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from lightmes.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 4: 写失败测试（连真实 DB）**

`tests/test_database.py`:
```python
from sqlalchemy import text
from lightmes.database import engine


def test_engine_connects_and_timescaledb_available():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        row = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname='timescaledb'")
        ).fetchone()
        assert row is not None
        assert row[0] == "timescaledb"
```

- [ ] **Step 5: 运行测试确认失败**

先确保 DB 已起（Task 2）。Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/test_database.py -v
```
Expected: 若 `database.py` 未写好则 ImportError；写好后本步应能连库。（此测试实为验证 Step 3 实现 + DB 连通，写好实现即通过。）

- [ ] **Step 6: 运行测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/test_database.py -v
```
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/shared src/lightmes/database.py tests/test_database.py pyproject.toml uv.lock
git commit -m "feat: add database engine, session and declarative base"
```

---

### Task 4: Alembic 迁移接线

接入 Alembic，`env.py` 从 `Base.metadata` 读取模型、从 `Settings` 读取连接串，支持 autogenerate。P0 此任务只建立机制（此时还没模型表，生成的是空基线；Task 5 会生成第一张 User 表迁移）。

**Files:**
- Create: `alembic.ini`
- Create: `src/lightmes/migrations/env.py`, `src/lightmes/migrations/script.py.mako`
- Create: `src/lightmes/migrations/versions/`（目录，放 `.gitkeep`）

**Interfaces:**
- Consumes: `lightmes.shared.base.Base`, `lightmes.config.get_settings`
- Produces: 可用的 `alembic revision --autogenerate` 和 `alembic upgrade head` 工作流；`env.py` 中 `target_metadata = Base.metadata`

- [ ] **Step 1: 加 Alembic 依赖**

Run: `uv add alembic`
Expected: 成功。

- [ ] **Step 2: 写 alembic.ini（精简版）**

`alembic.ini`:
```ini
[alembic]
script_location = src/lightmes/migrations
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 3: 写 script.py.mako**

`src/lightmes/migrations/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: 写 env.py**

`src/lightmes/migrations/env.py`:
```python
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from lightmes.config import get_settings
from lightmes.shared.base import Base
# 导入所有模型，确保它们注册到 Base.metadata（Task 5 起逐步加）
from lightmes.modules.auth import models as _auth_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

注意：`env.py` 里 import 了 `auth.models`。该文件在 Task 5 创建。为使本任务可独立验证，先创建占位包：

`src/lightmes/modules/__init__.py`: 空。
`src/lightmes/modules/auth/__init__.py`: 空。
`src/lightmes/modules/auth/models.py`: 暂时只放 `# placeholder, filled in Task 5`（一行注释）。

`src/lightmes/migrations/versions/.gitkeep`: 空文件。

- [ ] **Step 5: 验证 Alembic 能连库并生成空基线**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run alembic revision -m "baseline"
```
Expected: 在 `versions/` 生成一个空的 baseline 迁移脚本，命令成功。

- [ ] **Step 6: 应用迁移**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run alembic upgrade head
```
Expected: 成功；DB 中出现 `alembic_version` 表。可选验证：
```bash
docker compose exec db psql -U mes -d lightmes -c "SELECT version_num FROM alembic_version;"
```

- [ ] **Step 7: Commit**

```bash
git add alembic.ini src/lightmes/migrations src/lightmes/modules
git commit -m "chore: wire up alembic migrations against Base.metadata"
```

---

### Task 5: 密码哈希（shared/security）

先做认证的最底层能力：密码哈希与校验，纯函数、可单测、不依赖 DB。

**Files:**
- Create: `src/lightmes/shared/security.py`
- Test: `tests/modules/auth/__init__.py`, `tests/modules/__init__.py`, `tests/modules/auth/test_security.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `lightmes.shared.security.hash_password(plain: str) -> str`
  - `lightmes.shared.security.verify_password(plain: str, hashed: str) -> bool`

- [ ] **Step 1: 加依赖**

Run: `uv add "passlib[bcrypt]"`
Expected: 成功。

- [ ] **Step 2: 写失败测试**

`tests/modules/__init__.py`, `tests/modules/auth/__init__.py`: 空文件。

`tests/modules/auth/test_security.py`:
```python
from lightmes.shared.security import hash_password, verify_password


def test_hash_is_not_plaintext():
    h = hash_password("secret123")
    assert h != "secret123"
    assert len(h) > 20


def test_verify_correct_password():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True


def test_verify_wrong_password():
    h = hash_password("secret123")
    assert verify_password("wrong", h) is False
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/modules/auth/test_security.py -v`
Expected: FAIL —— `ImportError`（`security` 无 `hash_password`）。

- [ ] **Step 4: 写实现**

`src/lightmes/shared/security.py`:
```python
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/modules/auth/test_security.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/shared/security.py tests/modules
git commit -m "feat: add password hashing utilities"
```

---

### Task 6: User 模型 + 迁移

定义 `User` ORM 模型，用 Alembic autogenerate 生成建表迁移并应用。

**Files:**
- Modify: `src/lightmes/modules/auth/models.py`（替换 Task 4 的占位注释）
- Create: `src/lightmes/migrations/versions/<auto>_create_user.py`（autogenerate 生成）
- Test: `tests/conftest.py`, `tests/modules/auth/test_repository.py`

**Interfaces:**
- Consumes: `lightmes.shared.base.Base`, `lightmes.shared.base.TimestampMixin`
- Produces:
  - `lightmes.modules.auth.models.User`，字段：`id: Mapped[int]`（PK）、`username: Mapped[str]`（唯一、索引）、`password_hash: Mapped[str]`、`display_name: Mapped[str]`、`role: Mapped[str]`（默认 `"operator"`）、`is_active: Mapped[bool]`（默认 True）、含 `created_at`/`updated_at`
  - 表名：`users`

- [ ] **Step 1: 写 User 模型**

`src/lightmes/modules/auth/models.py`（覆盖占位）:
```python
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str] = mapped_column()
    display_name: Mapped[str] = mapped_column()
    role: Mapped[str] = mapped_column(default="operator")
    is_active: Mapped[bool] = mapped_column(default=True)
```

- [ ] **Step 2: 生成迁移**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run alembic revision --autogenerate -m "create user"
```
Expected: `versions/` 下生成含 `op.create_table("users", ...)` 的迁移脚本。打开确认包含 `username` 唯一约束。

- [ ] **Step 3: 应用迁移**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run alembic upgrade head
```
Expected: 成功。验证：
```bash
docker compose exec db psql -U mes -d lightmes -c "\d users"
```
应显示 `users` 表结构。

- [ ] **Step 4: 写测试 fixture（conftest）**

`tests/conftest.py`:
```python
import os
import pytest
from sqlalchemy import text
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
```

- [ ] **Step 5: 写失败测试（repository 尚不存在则先测模型可持久化）**

`tests/modules/auth/test_repository.py`:
```python
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
```

- [ ] **Step 6: 运行测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/modules/auth/test_repository.py -v
```
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/auth/models.py src/lightmes/migrations/versions tests/conftest.py tests/modules/auth/test_repository.py
git commit -m "feat: add User model and migration"
```

---

### Task 7: Auth Repository + Service

数据访问（repository）与业务逻辑（service，模块唯一对外接口）。service 提供"按用户名认证""创建用户"两个能力。

**Files:**
- Create: `src/lightmes/modules/auth/repository.py`
- Create: `src/lightmes/modules/auth/schemas.py`
- Create: `src/lightmes/modules/auth/service.py`
- Test: `tests/modules/auth/test_service.py`

**Interfaces:**
- Consumes: `lightmes.modules.auth.models.User`, `lightmes.shared.security.hash_password`, `lightmes.shared.security.verify_password`, SQLAlchemy `Session`
- Produces:
  - `repository.UserRepository(db: Session)`，方法：`get_by_username(username: str) -> User | None`, `add(user: User) -> User`
  - `schemas.UserCreate`（Pydantic：`username: str`, `password: str`, `display_name: str`, `role: str = "operator"`）
  - `schemas.UserRead`（Pydantic：`id: int`, `username: str`, `display_name: str`, `role: str`, `is_active: bool`；`model_config = ConfigDict(from_attributes=True)`）
  - `service.AuthService(db: Session)`，方法：
    - `create_user(data: UserCreate) -> User`（哈希密码后落库）
    - `authenticate(username: str, password: str) -> User | None`（成功返回 User，失败返回 None）

- [ ] **Step 1: 写 repository**

`src/lightmes/modules/auth/repository.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.auth.models import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return self.db.execute(stmt).scalar_one_or_none()

    def add(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()
        return user
```

- [ ] **Step 2: 写 schemas**

`src/lightmes/modules/auth/schemas.py`:
```python
from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "operator"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
```

- [ ] **Step 3: 写失败测试**

`tests/modules/auth/test_service.py`:
```python
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
```

- [ ] **Step 4: 运行测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/modules/auth/test_service.py -v
```
Expected: FAIL —— `ImportError`（`service` 尚不存在）。

- [ ] **Step 5: 写 service 实现**

`src/lightmes/modules/auth/service.py`:
```python
from sqlalchemy.orm import Session
from lightmes.modules.auth.models import User
from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.auth.schemas import UserCreate
from lightmes.shared.security import hash_password, verify_password


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = UserRepository(db)

    def create_user(self, data: UserCreate) -> User:
        user = User(
            username=data.username,
            password_hash=hash_password(data.password),
            display_name=data.display_name,
            role=data.role,
        )
        return self.repo.add(user)

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.repo.get_by_username(username)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user
```

- [ ] **Step 6: 运行测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/modules/auth/test_service.py -v
```
Expected: PASS（4 passed）。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/auth/repository.py src/lightmes/modules/auth/schemas.py src/lightmes/modules/auth/service.py tests/modules/auth/test_service.py
git commit -m "feat: add auth repository and service"
```

---

### Task 8: 库内事件总线骨架

进程内事件总线。P0 只建机制并单测，P1 起各模块真正 publish/subscribe。同步分发，接口预留将来异步。

**Files:**
- Create: `src/lightmes/shared/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `events.Event`（基类，`dataclass`，无字段，供具体事件继承）
  - `events.EventBus`，方法：
    - `subscribe(event_type: type[Event], handler: Callable[[Event], None]) -> None`
    - `publish(event: Event) -> None`（同步调用所有已订阅该类型的 handler）
  - `events.event_bus`（模块级单例 `EventBus()`）

- [ ] **Step 1: 写失败测试**

`tests/test_events.py`:
```python
from dataclasses import dataclass
from lightmes.shared.events import Event, EventBus


@dataclass
class SampleEvent(Event):
    value: int


def test_subscribe_and_publish_calls_handler():
    bus = EventBus()
    received = []
    bus.subscribe(SampleEvent, lambda e: received.append(e.value))
    bus.publish(SampleEvent(value=42))
    assert received == [42]


def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish(SampleEvent(value=1))  # 不应抛异常


def test_multiple_handlers_all_called():
    bus = EventBus()
    calls = []
    bus.subscribe(SampleEvent, lambda e: calls.append("a"))
    bus.subscribe(SampleEvent, lambda e: calls.append("b"))
    bus.publish(SampleEvent(value=0))
    assert sorted(calls) == ["a", "b"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_events.py -v`
Expected: FAIL —— `ImportError`（`events` 无 `Event`/`EventBus`）。

- [ ] **Step 3: 写实现**

`src/lightmes/shared/events.py`:
```python
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Event:
    """所有领域事件的基类。"""


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: Callable[[Event], None]) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in self._handlers[type(event)]:
            handler(event)


event_bus = EventBus()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_events.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/shared/events.py tests/test_events.py
git commit -m "feat: add in-process event bus skeleton"
```

---

### Task 9: 登录 API + HTMX 页面（端到端竖切收尾）

把 auth service 接到 FastAPI 路由：一个 JSON 登录 API + 一个 HTMX 登录页面。用基于 cookie 的会话（签名 cookie，简单起步）。这一步让"骨架从 DB 到浏览器全通"。

**Files:**
- Create: `src/lightmes/modules/auth/router.py`
- Create: `src/lightmes/templates/base.html`, `src/lightmes/templates/login.html`
- Create: `src/lightmes/static/vendor/htmx.min.js`（本地化第三方库，不走 CDN）
- Modify: `src/lightmes/main.py`（挂载路由、静态目录、模板、session 中间件）
- Test: `tests/modules/auth/test_router.py`

**安全/部署说明：** 车间厂内服务器多为内网、可能无外网。前端第三方库（htmx）一律**本地托管**，不引用公共 CDN。这既避免 CDN 被投毒/不可达的风险，也保证内网可离线运行。

**Interfaces:**
- Consumes: `lightmes.modules.auth.service.AuthService`, `lightmes.database.get_db`, `lightmes.config.get_settings`
- Produces:
  - `POST /api/auth/login`（表单或 JSON：`username`,`password`）→ 成功 200 `{"username":..., "display_name":...}` 并写 session cookie；失败 401 `{"detail":"用户名或密码错误"}`
  - `GET /login` → 返回 HTMX 登录页面（HTML）
  - `POST /login`（HTMX 表单提交）→ 成功返回含成功提示的 HTML 片段并写 session；失败返回含错误提示的 HTML 片段（HTTP 200，错误信息在页面内）
  - `router` 对象：`lightmes.modules.auth.router.router`（APIRouter）

- [ ] **Step 1: 加 session 中间件依赖**

Run: `uv add itsdangerous`
Expected: 成功（`SessionMiddleware` 依赖 itsdangerous）。

- [ ] **Step 2: 本地化 htmx（不走 CDN）**

下载 htmx 到本地静态目录（内网/离线可用，规避 CDN 风险）。Run:
```bash
mkdir -p src/lightmes/static/vendor
curl -fsSL https://unpkg.com/htmx.org@2.0.2/dist/htmx.min.js -o src/lightmes/static/vendor/htmx.min.js
test -s src/lightmes/static/vendor/htmx.min.js && echo "htmx downloaded"
```
Expected: 打印 `htmx downloaded`，文件非空。（若开发机也无外网，从有网机器拷贝该文件到此路径即可。）

- [ ] **Step 3: 写模板（引用本地 htmx）**

`src/lightmes/templates/base.html`:
```html
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>{% block title %}LightMES{% endblock %}</title>
  <script src="/static/vendor/htmx.min.js"></script>
</head>
<body>
  {% block content %}{% endblock %}
</body>
</html>
```

`src/lightmes/templates/login.html`:
```html
{% extends "base.html" %}
{% block title %}登录 - LightMES{% endblock %}
{% block content %}
<h1>LightMES 登录</h1>
<form hx-post="/login" hx-target="#result" hx-swap="innerHTML">
  <input type="text" name="username" placeholder="用户名" required>
  <input type="password" name="password" placeholder="密码" required>
  <button type="submit">登录</button>
</form>
<div id="result">{{ message | default("") }}</div>
{% endblock %}
```

- [ ] **Step 4: 写 router**

`src/lightmes/modules/auth/router.py`:
```python
from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService

router = APIRouter()
templates = Jinja2Templates(directory="src/lightmes/templates")


@router.post("/api/auth/login")
def api_login(
    response: Response,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    user = AuthService(db).authenticate(username, password)
    if user is None:
        return JSONResponse(
            {"detail": "用户名或密码错误"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.session["user_id"] = user.id
    return JSONResponse({"username": user.username, "display_name": user.display_name})


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html")


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = AuthService(db).authenticate(username, password)
    if user is None:
        return HTMLResponse('<span style="color:red">用户名或密码错误</span>')
    request.session["user_id"] = user.id
    return HTMLResponse(f'<span style="color:green">欢迎，{user.display_name}</span>')
```

- [ ] **Step 5: 改 main.py 挂载路由、静态目录与 session**

`src/lightmes/main.py`（覆盖）:
```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from lightmes.config import get_settings
from lightmes.modules.auth.router import router as auth_router

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount("/static", StaticFiles(directory="src/lightmes/static"), name="static")
app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
```

- [ ] **Step 6: 写失败测试**

`tests/modules/auth/test_router.py`:
```python
import pytest
from fastapi.testclient import TestClient

from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate


@pytest.fixture()
def client(db_session):
    # 用测试事务 session 覆盖 get_db 依赖
    app.dependency_overrides[get_db] = lambda: db_session
    # 预置一个用户
    AuthService(db_session).create_user(
        UserCreate(username="eve", password="pw12345", display_name="Eve")
    )
    db_session.flush()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_login_success(client):
    resp = client.post(
        "/api/auth/login", data={"username": "eve", "password": "pw12345"}
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "eve"


def test_api_login_failure(client):
    resp = client.post(
        "/api/auth/login", data={"username": "eve", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "LightMES 登录" in resp.text


def test_login_submit_success_returns_welcome(client):
    resp = client.post("/login", data={"username": "eve", "password": "pw12345"})
    assert resp.status_code == 200
    assert "欢迎" in resp.text
```

- [ ] **Step 7: 运行测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/modules/auth/test_router.py -v
```
Expected: FAIL —— 路由/模板未接线（ImportError 或 404）。

- [ ] **Step 8: 运行测试确认通过**

先确认 Step 2–5 已落地。Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest tests/modules/auth/test_router.py -v
```
Expected: PASS（4 passed）。

- [ ] **Step 9: 全量测试回归**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@localhost:5432/lightmes" uv run pytest -v
```
Expected: 全绿（Task 1/3/5/6/7/8/9 所有测试）。

- [ ] **Step 10: Commit**

```bash
git add src/lightmes/modules/auth/router.py src/lightmes/templates src/lightmes/static src/lightmes/main.py tests/modules/auth/test_router.py pyproject.toml uv.lock
git commit -m "feat: add login API and HTMX login page (end-to-end slice)"
```

---

### Task 10: 基础 CI（GitHub Actions）

CI 在 push/PR 时跑：起 PostgreSQL(TimescaleDB) 服务、装依赖、跑迁移、跑全量测试。保证骨架在干净环境下可复现。

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `pyproject.toml`/`uv.lock`、Alembic 迁移、pytest 测试
- Produces: 一个绿色的 CI 工作流

- [ ] **Step 1: 写 CI 工作流**

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      db:
        image: timescale/timescaledb:latest-pg16
        env:
          POSTGRES_USER: mes
          POSTGRES_PASSWORD: mes
          POSTGRES_DB: lightmes
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U mes -d lightmes"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://mes:mes@localhost:5432/lightmes
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
      - name: Set up Python
        run: uv python install 3.12
      - name: Install dependencies
        run: uv sync --frozen
      - name: Run migrations
        run: uv run alembic upgrade head
      - name: Run tests
        run: uv run pytest -v
```

- [ ] **Step 2: 本地静态校验工作流（可选自检）**

Run:
```bash
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml valid')"
```
Expected: 打印 `ci.yml valid`（YAML 语法无误）。若本地无 pyyaml，跳过本步，靠远端 CI 校验。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "chore: add CI workflow running migrations and tests"
```

---

## Self-Review 结果

**Spec 覆盖检查**（对照 spec §3/§4/§7/§9/§11）：
- 技术栈 Python+FastAPI+SQLAlchemy+Alembic+Jinja2/HTMX+PG/Timescale+MQTT+pytest+uv+Docker → Task 1/2/3/4/9/10 覆盖 ✅
- 模块化单体骨架（modules/ + shared/）→ Task 1/3/5/7/9 ✅
- Docker Compose（PG+Timescale+Mosquitto）→ Task 2 ✅
- 核心领域模型 + Alembic 迁移基线 → Task 3/4/6（P0 只落地 auth 的 User；P1 主数据模型另立计划）✅
- 简单认证（本地账号/登录）→ Task 5/6/7/9 ✅
- 库内事件总线骨架 → Task 8 ✅
- pytest + CI → 全程测试 + Task 10 ✅
- 端到端竖切（API+页面）→ Task 9 ✅
- 集成测试用真实 PG → conftest（Task 6）+ CI service（Task 10）✅

**说明**：P0 范围内数据模型只落地 auth 的 `User`（作为贯通全栈的载体）。spec §7 的业务主数据（product/station/routing/bom/work_order/serial_unit/sn_rule/station_pass/genealogy_bind）属于 **P1**，将在 P0 骨架跑通后另立实现计划——这与 spec §13 "P1 待 P0 骨架跑通后另立计划"一致，非遗漏。

**占位符扫描**：无 TBD/TODO/"稍后实现"；每个代码步骤均含完整代码。

**类型一致性**：`Settings`/`get_settings`、`Base`/`TimestampMixin`、`get_db`/`SessionLocal`/`engine`、`User`、`UserRepository.get_by_username/add`、`UserCreate`/`UserRead`、`AuthService.create_user/authenticate`、`Event`/`EventBus.subscribe/publish`、`auth.router.router` —— 定义处与引用处签名一致 ✅。
