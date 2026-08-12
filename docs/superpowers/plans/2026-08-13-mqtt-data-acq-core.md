# MQTT 数采核心栈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 LightMES 的 MQTT 协议核心数采栈：DB 驱动热配置的连接/Topic 管理 + 独立监听进程接收消息入库 + Admin UI 浏览。

**Architecture:** 4 张新表（machine_connections / mqtt_connections / machine_topics / machine_messages）+ Fernet 对称加密密码字段 + aiomqtt async 客户端 + asyncio supervisor 进程（5s reconcile DB，单进程多连接，指数退避重连）+ HTMX admin UI。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2+HTMX, aiomqtt 2.3+, cryptography 45+ (Fernet), PostgreSQL, pytest, uv

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（必须 127.0.0.1，不用 localhost — Windows IPv6 ~130s 卡顿）
- 测试用 `db_session` fixture（SAVEPOINT 隔离），不直接 commit；service 层可 commit
- Service 层抛 `DomainError` 子类（`BusinessRuleError` / `ValidationError` / `NotFoundError`）从 `lightmes.shared.errors`
- 文案 Chinese for all UI/error strings
- 监听器是**独立进程**（`python -m lightmes.connectivity.mqtt_listener`），不走 FastAPI lifespan
- 监听器每 5 秒 reconcile DB；新增/停用 connection 自动生效
- Topic 通配符：MQTT 标准 `+` 单层、`#` 多层后缀
- 密码字段用 `cryptography.fernet.Fernet` 对称加密；明文永不进 DB
- 复用 `settings.secret_key`（来自 `lightmes.config.get_settings()`）派生 Fernet key（PBKDF2-HMAC-SHA256，salt 固定为 "lightmes-mqtt-encryption-salt"）
- `machine_messages` 表是 append-only（无 `updated_at`）
- `machine_connections.messages_received` 递增用 SQL UPDATE（避免 race condition）
- 删除 `machine_connection` 级联：mqtt_connections CASCADE、machine_topics CASCADE、machine_messages CASCADE
- 删除 `machine_topic` 时 `machine_messages.matched_topic_id` SET NULL
- 最新 migration ID = `c9e5a1b34f6a`（Task 1 的 down_revision）
- 新增依赖：`aiomqtt>=2.3,<3.0` + `cryptography>=45.0.0`
- 模块注册通过 `lightmes/modules/connectivity/__init__.py` 的 `register(app)` 函数
- Admin UI 用现有 LightMES 风格（.card/.form-row/.data-table），不走 planner.css
- Admin routes 用 `Depends(require_role("admin", "supervisor"))`
- 测试不依赖真实 MQTT broker（broker tests 默认 skip）

---

### Task 1: 依赖 + 模块结构 + Migration + Models + crypto + topic_match

**Files:**
- Modify: `pyproject.toml`（加 aiomqtt + cryptography 依赖）
- Create: `src/lightmes/modules/connectivity/__init__.py`（register stub）
- Create: `src/lightmes/modules/connectivity/models.py`（4 张表的 SQLAlchemy 模型）
- Create: `src/lightmes/modules/connectivity/crypto.py`（Fernet 加密/解密）
- Create: `src/lightmes/modules/connectivity/topic_match.py`（MQTT 通配符匹配）
- Create: `src/lightmes/migrations/versions/d0f6b2c75a8e_add_connectivity_tables.py`
- Modify: `src/lightmes/main.py`（注册 connectivity 模块）
- Modify: `tests/conftest.py`（注册 connectivity models 到 Base.metadata）
- Create: `tests/modules/connectivity/__init__.py`（空）
- Create: `tests/modules/connectivity/test_topic_match.py`
- Create: `tests/modules/connectivity/test_crypto.py`
- Create: `tests/modules/connectivity/test_models.py`

**Interfaces:**
- Consumes: 无（首个 task）
- Produces:
  - `MachineConnection` / `MqttConnection` / `MachineTopic` / `MachineMessage` 模型
  - `matches_topic(pattern: str, topic: str) -> bool` 通配符匹配
  - `encrypt_password(plaintext: str) -> str` + `decrypt_password(ciphertext: str | None) -> str | None`（None 输入返 None；解密失败返 None 不抛）
  - Migration `d0f6b2c75a8e`，down_revision = `c9e5a1b34f6a`

- [ ] **Step 1: 加依赖**

修改 `pyproject.toml`，在 `dependencies` 数组中添加（按字母顺序插入）：

```toml
dependencies = [
    "aiomqtt>=2.3.0,<3.0",
    "alembic>=1.18.5",
    # ... existing ...
    "cryptography>=45.0.0",
    # ... existing ...
]
```

执行 `uv sync`。

验证：

```bash
uv run python -c "import aiomqtt; import cryptography.fernet; print('OK')"
```

- [ ] **Step 2: 写失败测试 - topic_match**

创建 `tests/modules/connectivity/__init__.py`（空）+ `tests/modules/connectivity/test_topic_match.py`：

```python
from lightmes.modules.connectivity.topic_match import matches_topic


def test_exact_match():
    assert matches_topic("machine/line1/count", "machine/line1/count") is True


def test_no_match():
    assert matches_topic("machine/line1/count", "machine/line1/alarm") is False


def test_plus_single_level_wildcard():
    assert matches_topic("machine/+/count", "machine/line1/count") is True
    assert matches_topic("machine/+/count", "machine/line2/count") is True
    assert matches_topic("machine/+/count", "machine/line1/alarm") is False
    # + 不匹配多层
    assert matches_topic("machine/+/count", "machine/line1/sub/count") is False


def test_hash_multi_level_wildcard():
    assert matches_topic("machine/line1/#", "machine/line1/count") is True
    assert matches_topic("machine/line1/#", "machine/line1/sub/deep/path") is True
    assert matches_topic("machine/line1/#", "machine/line2/count") is False
    # 顶层 # 匹配所有
    assert matches_topic("#", "anything/anywhere") is True
    assert matches_topic("#", "top") is True


def test_plus_and_hash_combined():
    assert matches_topic("+/line1/#", "a/line1/b/c") is True
    assert matches_topic("+/line1/#", "a/line2/b/c") is False
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/modules/connectivity/test_topic_match.py -v`
Expected: ImportError on `topic_match`。

- [ ] **Step 4: 实现 topic_match**

创建 `src/lightmes/modules/connectivity/topic_match.py`：

```python
"""MQTT topic wildcard matching (MQTT spec: + = single level, # = multi level suffix)."""


def matches_topic(pattern: str, topic: str) -> bool:
    """Check if a topic matches an MQTT subscription pattern.

    MQTT wildcards:
        + : exactly one level (e.g. "machine/+/count" matches "machine/L1/count")
        # : zero or more levels at end (e.g. "machine/#" matches "machine/L1/a/b")
    """
    if pattern == topic:
        return True

    # # multi-level suffix
    if pattern.endswith("#"):
        prefix = pattern[:-1].rstrip("/")
        # prefix="" means "#" matches everything
        # prefix="machine/line1" matches "machine/line1" + anything under it
        return prefix == "" or topic == prefix or topic.startswith(prefix + "/")

    # + single-level per segment
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    if len(pattern_parts) != len(topic_parts):
        return False
    for p, t in zip(pattern_parts, topic_parts):
        if p != "+" and p != t:
            return False
    return True
```

- [ ] **Step 5: 运行 topic_match 测试**

Run: `uv run pytest tests/modules/connectivity/test_topic_match.py -v`
Expected: 5 tests PASS。

- [ ] **Step 6: 写失败测试 - crypto**

创建 `tests/modules/connectivity/test_crypto.py`：

```python
from lightmes.modules.connectivity.crypto import encrypt_password, decrypt_password


def test_encrypt_decrypt_round_trip():
    plaintext = "s3cr3t-pa$$w0rd"
    encrypted = encrypt_password(plaintext)
    assert encrypted != plaintext
    assert encrypted.startswith("gAAAAA")  # Fernet token prefix
    assert decrypt_password(encrypted) == plaintext


def test_encrypt_distinct_each_call():
    """Each encryption produces different ciphertext (Fernet random IV)."""
    p = "same-password"
    e1 = encrypt_password(p)
    e2 = encrypt_password(p)
    assert e1 != e2
    assert decrypt_password(e1) == decrypt_password(e2) == p


def test_decrypt_none_returns_none():
    assert decrypt_password(None) is None


def test_decrypt_empty_returns_none():
    assert decrypt_password("") is None


def test_decrypt_invalid_returns_none():
    """Wrong key / corrupted ciphertext → None (no exception)."""
    assert decrypt_password("not-a-valid-fernet-token") is None
```

- [ ] **Step 7: 运行测试，确认失败**

Run: `uv run pytest tests/modules/connectivity/test_crypto.py -v`
Expected: ImportError on `crypto`。

- [ ] **Step 8: 实现 crypto**

创建 `src/lightmes/modules/connectivity/crypto.py`：

```python
"""Password encryption for MQTT connection credentials (Fernet symmetric)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from lightmes.config import get_settings

_SALT = b"lightmes-mqtt-encryption-salt"  # fixed salt (single-tenant internal MES)

# Lazy-init Fernet (settings loaded once at import time)
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        secret = get_settings().secret_key.encode("utf-8")
        key = hashlib.pbkdf2_hmac("sha256", secret, _SALT, iterations=100_000, dklen=32)
        _fernet = Fernet(base64.urlsafe_b64encode(key))
    return _fernet


def encrypt_password(plaintext: str) -> str:
    """Encrypt a plaintext password → Fernet token string."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_password(ciphertext: str | None) -> str | None:
    """Decrypt a Fernet token. Returns None on None/empty/invalid input (no exception)."""
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return None
```

- [ ] **Step 9: 运行 crypto 测试**

Run: `uv run pytest tests/modules/connectivity/test_crypto.py -v`
Expected: 5 tests PASS。

- [ ] **Step 10: 实现 models**

创建 `src/lightmes/modules/connectivity/models.py`：

```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from lightmes.shared.base import Base, TimestampMixin


class MachineConnection(Base, TimestampMixin):
    __tablename__ = "machine_connections"
    __table_args__ = (
        UniqueConstraint("name", name="uq_machine_connection_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    protocol: Mapped[str] = mapped_column(String(20), default="mqtt")
    is_active: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    status_message: Mapped[str | None] = mapped_column(String(500), default=None)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    messages_received: Mapped[int] = mapped_column(Integer, default=0)


class MqttConnection(Base, TimestampMixin):
    __tablename__ = "mqtt_connections"
    __table_args__ = (
        UniqueConstraint("machine_connection_id", name="uq_mqtt_per_machine_connection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    broker_host: Mapped[str] = mapped_column(String(255))
    broker_port: Mapped[int] = mapped_column(Integer, default=1883)
    client_id: Mapped[str | None] = mapped_column(String(100), default=None)
    username: Mapped[str | None] = mapped_column(String(100), default=None)
    password_encrypted: Mapped[str | None] = mapped_column(String(500), default=None)
    use_tls: Mapped[bool] = mapped_column(default=False)
    keep_alive_seconds: Mapped[int] = mapped_column(Integer, default=60)
    qos_default: Mapped[int] = mapped_column(Integer, default=0)
    clean_session: Mapped[bool] = mapped_column(default=True)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    reconnect_delay_seconds: Mapped[int] = mapped_column(Integer, default=5)


class MachineTopic(Base, TimestampMixin):
    __tablename__ = "machine_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    topic_pattern: Mapped[str] = mapped_column(String(500))
    payload_format: Mapped[str] = mapped_column(String(20), default="json")
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class MachineMessage(Base):
    """Append-only log — no updated_at."""
    __tablename__ = "machine_messages"
    __table_args__ = (
        Index("ix_machine_messages_conn_received", "machine_connection_id", "received_at"),
        Index("ix_machine_messages_received", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(500))
    raw_payload: Mapped[str] = mapped_column(Text)
    matched_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("machine_topics.id", ondelete="SET NULL"), default=None)
    processing_status: Mapped[str] = mapped_column(String(20), default="ok")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 11: 写 models 单元测试**

创建 `tests/modules/connectivity/test_models.py`：

```python
from lightmes.modules.connectivity.models import (
    MachineConnection, MqttConnection, MachineTopic, MachineMessage,
)


def test_machine_connection_basic_fields(db_session):
    c = MachineConnection(name="test-conn", protocol="mqtt")
    db_session.add(c); db_session.flush()
    assert c.id is not None
    assert c.protocol == "mqtt"
    assert c.is_active is False
    assert c.status == "disconnected"
    assert c.messages_received == 0


def test_mqtt_connection_basic_fields(db_session):
    c = MachineConnection(name="test-mqtt")
    db_session.add(c); db_session.flush()
    m = MqttConnection(
        machine_connection_id=c.id, broker_host="broker.local", broker_port=1883)
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.broker_host == "broker.local"
    assert m.broker_port == 1883
    assert m.keep_alive_seconds == 60
    assert m.qos_default == 0


def test_machine_topic_basic_fields(db_session):
    c = MachineConnection(name="test-topic")
    db_session.add(c); db_session.flush()
    t = MachineTopic(
        machine_connection_id=c.id, topic_pattern="machine/+/count",
        payload_format="json")
    db_session.add(t); db_session.flush()
    assert t.id is not None
    assert t.is_active is True
    assert t.topic_pattern == "machine/+/count"


def test_machine_message_basic_fields(db_session):
    from datetime import datetime, timezone
    c = MachineConnection(name="test-msg")
    db_session.add(c); db_session.flush()
    m = MachineMessage(
        machine_connection_id=c.id, topic="machine/L1/count",
        raw_payload='{"count": 1}',
        received_at=datetime.now(timezone.utc))
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.processing_status == "ok"
    assert m.matched_topic_id is None
```

- [ ] **Step 12: 运行 models 测试**

Run: `uv run pytest tests/modules/connectivity/test_models.py -v`
Expected: 4 tests PASS。

- [ ] **Step 13: 创建 Alembic 迁移**

创建 `src/lightmes/migrations/versions/d0f6b2c75a8e_add_connectivity_tables.py`：

```python
"""add_connectivity_tables

Revision ID: d0f6b2c75a8e
Revises: c9e5a1b34f6a
Create Date: 2026-08-13 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd0f6b2c75a8e'
down_revision = 'c9e5a1b34f6a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('machine_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('protocol', sa.String(length=20), nullable=False, server_default='mqtt'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='disconnected'),
        sa.Column('status_message', sa.String(length=500), nullable=True),
        sa.Column('last_connected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('messages_received', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_machine_connection_name'),
    )
    op.create_table('mqtt_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('broker_host', sa.String(length=255), nullable=False),
        sa.Column('broker_port', sa.Integer(), nullable=False, server_default='1883'),
        sa.Column('client_id', sa.String(length=100), nullable=True),
        sa.Column('username', sa.String(length=100), nullable=True),
        sa.Column('password_encrypted', sa.String(length=500), nullable=True),
        sa.Column('use_tls', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('keep_alive_seconds', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('qos_default', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clean_session', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('connect_timeout_seconds', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('reconnect_delay_seconds', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('machine_connection_id', name='uq_mqtt_per_machine_connection'),
        sa.ForeignKeyConstraint(['machine_connection_id'], ['machine_connections.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_mqtt_connections_machine_connection_id',
                    'mqtt_connections', ['machine_connection_id'])
    op.create_table('machine_topics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('topic_pattern', sa.String(length=500), nullable=False),
        sa.Column('payload_format', sa.String(length=20), nullable=False, server_default='json'),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_connection_id'], ['machine_connections.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_machine_topics_machine_connection_id',
                    'machine_topics', ['machine_connection_id'])
    op.create_table('machine_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=500), nullable=False),
        sa.Column('raw_payload', sa.Text(), nullable=False),
        sa.Column('matched_topic_id', sa.Integer(), nullable=True),
        sa.Column('processing_status', sa.String(length=20), nullable=False, server_default='ok'),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_connection_id'], ['machine_connections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matched_topic_id'], ['machine_topics.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_machine_messages_machine_connection_id',
                    'machine_messages', ['machine_connection_id'])
    op.create_index('ix_machine_messages_conn_received',
                    'machine_messages', ['machine_connection_id', 'received_at'])
    op.create_index('ix_machine_messages_received',
                    'machine_messages', ['received_at'])


def downgrade() -> None:
    op.drop_index('ix_machine_messages_received', table_name='machine_messages')
    op.drop_index('ix_machine_messages_conn_received', table_name='machine_messages')
    op.drop_index('ix_machine_messages_machine_connection_id', table_name='machine_messages')
    op.drop_table('machine_messages')
    op.drop_index('ix_machine_topics_machine_connection_id', table_name='machine_topics')
    op.drop_table('machine_topics')
    op.drop_index('ix_mqtt_connections_machine_connection_id', table_name='mqtt_connections')
    op.drop_table('mqtt_connections')
    op.drop_table('machine_connections')
```

- [ ] **Step 14: 应用迁移验证**

Run: `uv run alembic upgrade head`
Expected: 输出 `Running upgrade c9e5a1b34f6a -> d0f6b2c75a8e, add_connectivity_tables`，无报错。

Run: `uv run alembic downgrade -1`
Expected: 输出 `Running downgrade d0f6b2c75a8e -> c9e5a1b34f6a`，无报错。

Run: `uv run alembic upgrade head`

- [ ] **Step 15: 注册 connectivity 模块**

创建 `src/lightmes/modules/connectivity/__init__.py`：

```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Register connectivity admin routes. Filled in Task 3."""
    # 触发 models 加载（确保 Base.metadata 注册）
    from lightmes.modules.connectivity import models  # noqa: F401
    # 后续 task 填充 router 注册
```

修改 `src/lightmes/main.py`，import + register：

```python
from lightmes.modules import (
    agent_gateway, api_v1, auth, connectivity, integration, masterdata, production, trace, quality,
)
```

在 `quality.register(app)` 之后追加（与其他模块平级）：

```python
connectivity.register(app)
```

修改 `tests/conftest.py`，加一行 import（与其他模块 import 同款）：

```python
from lightmes.modules.connectivity import models as _connectivity_models  # noqa: F401
```

- [ ] **Step 16: 运行全套 connectivity 测试**

Run: `uv run pytest tests/modules/connectivity/ -v`
Expected: 全部 PASS（5 topic_match + 5 crypto + 4 models = 14 tests）。

- [ ] **Step 17: Commit**

```bash
git add pyproject.toml uv.lock \
        src/lightmes/modules/connectivity/__init__.py \
        src/lightmes/modules/connectivity/models.py \
        src/lightmes/modules/connectivity/crypto.py \
        src/lightmes/modules/connectivity/topic_match.py \
        src/lightmes/migrations/versions/d0f6b2c75a8e_add_connectivity_tables.py \
        src/lightmes/main.py \
        tests/conftest.py \
        tests/modules/connectivity/__init__.py \
        tests/modules/connectivity/test_topic_match.py \
        tests/modules/connectivity/test_crypto.py \
        tests/modules/connectivity/test_models.py
git commit -m "feat(connectivity): 4 models + Fernet crypto + topic wildcard matching + migration"
```

---

### Task 2: Repository + Service（CRUD + 业务约束）

**Files:**
- Create: `src/lightmes/modules/connectivity/repository.py`
- Create: `src/lightmes/modules/connectivity/schemas.py`
- Create: `src/lightmes/modules/connectivity/service.py`
- Test: `tests/modules/connectivity/test_service.py`

**Interfaces:**
- Consumes: Task 1 的 4 个 model + crypto + topic_match
- Produces:
  - `MachineConnectionRepository` / `MqttConnectionRepository` / `MachineTopicRepository` / `MachineMessageRepository`
  - `ConnectivityService`：
    - `create_connection(name, broker_host, broker_port, username, password, ...) -> tuple[MachineConnection, MqttConnection]`
    - `get_connection(conn_id) -> MachineConnection | None`
    - `list_connections() -> list[MachineConnection]`
    - `activate_connection(conn_id) -> MachineConnection` / `deactivate_connection(conn_id)`
    - `delete_connection(conn_id)`
    - `add_topic(conn_id, topic_pattern, payload_format, description) -> MachineTopic`
    - `toggle_topic(conn_id, topic_id) -> MachineTopic` / `delete_topic(conn_id, topic_id)`
    - `list_topics(conn_id) -> list[MachineTopic]`
    - `list_recent_messages(conn_id, limit=100) -> list[MachineMessage]`
  - `ConnectivityService` 内部抛 `ValidationError`（payload_format 非 4 值之一 / protocol 非 mqtt / port 范围 / qos 范围）和 `BusinessRuleError`（重名 / 重复 active topic）和 `NotFoundError`

- [ ] **Step 1: 写失败测试 - service**

创建 `tests/modules/connectivity/test_service.py`：

```python
import pytest
from lightmes.modules.connectivity.service import ConnectivityService
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


def test_create_connection_returns_pair_with_encrypted_password(db_session):
    svc = ConnectivityService(db_session)
    conn, mqtt = svc.create_connection(
        name="t-conn-1", broker_host="broker.local", broker_port=1883,
        username="user", password="s3cret", use_tls=False)
    assert conn.id is not None
    assert conn.name == "t-conn-1"
    assert conn.protocol == "mqtt"
    assert mqtt.broker_host == "broker.local"
    assert mqtt.password_encrypted is not None
    assert mqtt.password_encrypted != "s3cret"  # 加密了


def test_create_connection_rejects_non_mqtt_protocol(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(ValidationError):
        svc.create_connection(name="bad", broker_host="x", broker_port=1883, protocol="opcua")


def test_create_connection_rejects_duplicate_name(db_session):
    svc = ConnectivityService(db_session)
    svc.create_connection(name="dup", broker_host="x", broker_port=1883)
    with pytest.raises(BusinessRuleError):
        svc.create_connection(name="dup", broker_host="y", broker_port=1883)


def test_create_connection_rejects_bad_port(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(ValidationError):
        svc.create_connection(name="bad-port", broker_host="x", broker_port=99999)


def test_create_connection_rejects_bad_qos(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(ValidationError):
        svc.create_connection(name="bad-qos", broker_host="x", broker_port=1883, qos_default=5)


def test_activate_and_deactivate(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="ad", broker_host="x", broker_port=1883)
    assert conn.is_active is False
    svc.activate_connection(conn.id)
    db_session.refresh(conn)
    assert conn.is_active is True
    svc.deactivate_connection(conn.id)
    db_session.refresh(conn)
    assert conn.is_active is False


def test_add_topic(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-topic", broker_host="x", broker_port=1883)
    t = svc.add_topic(conn.id, "machine/+/count", "json", "test topic")
    assert t.id is not None
    assert t.topic_pattern == "machine/+/count"
    assert t.is_active is True


def test_add_topic_rejects_invalid_format(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="bad-fmt", broker_host="x", broker_port=1883)
    with pytest.raises(ValidationError):
        svc.add_topic(conn.id, "machine/x", "xml")  # xml 不在 4 个允许值中


def test_add_topic_rejects_duplicate_active(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="dup-t", broker_host="x", broker_port=1883)
    svc.add_topic(conn.id, "machine/+/count", "json")
    # 同样 pattern + active=True 应该被拒
    with pytest.raises(BusinessRuleError):
        svc.add_topic(conn.id, "machine/+/count", "json")


def test_toggle_topic(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-toggle", broker_host="x", broker_port=1883)
    t = svc.add_topic(conn.id, "machine/x", "json")
    svc.toggle_topic(conn.id, t.id)
    db_session.refresh(t)
    assert t.is_active is False
    svc.toggle_topic(conn.id, t.id)
    db_session.refresh(t)
    assert t.is_active is True


def test_delete_topic(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-del", broker_host="x", broker_port=1883)
    t = svc.add_topic(conn.id, "machine/x", "json")
    svc.delete_topic(conn.id, t.id)
    assert svc.list_topics(conn.id) == []


def test_delete_connection_cascades(db_session):
    svc = ConnectivityService(db_session)
    conn, mqtt = svc.create_connection(name="cascade", broker_host="x", broker_port=1883)
    svc.add_topic(conn.id, "machine/x", "json")
    svc.delete_connection(conn.id)
    from lightmes.modules.connectivity.models import MqttConnection, MachineTopic
    assert db_session.get(MqttConnection, mqtt.id) is None
    assert svc.list_topics(conn.id) == []


def test_get_connection_not_found(db_session):
    svc = ConnectivityService(db_session)
    with pytest.raises(NotFoundError):
        svc.get_connection(99999)


def test_list_recent_messages_empty(db_session):
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(name="t-msg", broker_host="x", broker_port=1883)
    msgs = svc.list_recent_messages(conn.id)
    assert msgs == []
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/connectivity/test_service.py -v`
Expected: ImportError on `service`。

- [ ] **Step 3: 实现 schemas**

创建 `src/lightmes/modules/connectivity/schemas.py`：

```python
from pydantic import BaseModel, ConfigDict, Field


class ConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    broker_host: str = Field(..., min_length=1, max_length=255)
    broker_port: int = Field(default=1883, ge=1, le=65535)
    username: str | None = None
    password: str | None = None  # 明文，service 层加密
    use_tls: bool = False
    keep_alive_seconds: int = 60
    qos_default: int = Field(default=0, ge=0, le=2)
    clean_session: bool = True
    connect_timeout_seconds: int = 10
    reconnect_delay_seconds: int = 5


class TopicCreate(BaseModel):
    topic_pattern: str = Field(..., min_length=1, max_length=500)
    payload_format: str = "json"  # json / plain / csv / hex
    description: str | None = None
```

- [ ] **Step 4: 实现 repository**

创建 `src/lightmes/modules/connectivity/repository.py`：

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.models import (
    MachineConnection, MachineMessage, MachineTopic, MqttConnection,
)


class MachineConnectionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, conn: MachineConnection) -> MachineConnection:
        self.db.add(conn); self.db.flush()
        return conn

    def get(self, conn_id: int) -> MachineConnection | None:
        return self.db.get(MachineConnection, conn_id)

    def get_by_name(self, name: str) -> MachineConnection | None:
        return self.db.execute(
            select(MachineConnection).where(MachineConnection.name == name)
        ).scalar_one_or_none()

    def list_all(self) -> list[MachineConnection]:
        return list(self.db.execute(
            select(MachineConnection).order_by(MachineConnection.id.desc())
        ).scalars().all())

    def list_active(self) -> list[MachineConnection]:
        return list(self.db.execute(
            select(MachineConnection).where(
                MachineConnection.is_active.is_(True),
                MachineConnection.protocol == "mqtt",
            )
        ).scalars().all())

    def delete(self, conn_id: int) -> None:
        c = self.get(conn_id)
        if c is not None:
            self.db.delete(c)
            self.db.flush()


class MqttConnectionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, mqtt: MqttConnection) -> MqttConnection:
        self.db.add(mqtt); self.db.flush()
        return mqtt

    def get_by_machine_connection(self, machine_conn_id: int) -> MqttConnection | None:
        return self.db.execute(
            select(MqttConnection).where(MqttConnection.machine_connection_id == machine_conn_id)
        ).scalar_one_or_none()


class MachineTopicRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, t: MachineTopic) -> MachineTopic:
        self.db.add(t); self.db.flush()
        return t

    def get(self, topic_id: int) -> MachineTopic | None:
        return self.db.get(MachineTopic, topic_id)

    def list_for_connection(self, conn_id: int) -> list[MachineTopic]:
        return list(self.db.execute(
            select(MachineTopic).where(MachineTopic.machine_connection_id == conn_id)
            .order_by(MachineTopic.id)
        ).scalars().all())

    def list_active_for_connection(self, conn_id: int) -> list[MachineTopic]:
        return list(self.db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == conn_id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all())

    def find_active_duplicate(self, conn_id: int, pattern: str) -> MachineTopic | None:
        return self.db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == conn_id,
                MachineTopic.topic_pattern == pattern,
                MachineTopic.is_active.is_(True),
            )
        ).scalar_one_or_none()

    def delete(self, topic_id: int) -> None:
        t = self.get(topic_id)
        if t is not None:
            self.db.delete(t)
            self.db.flush()


class MachineMessageRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, m: MachineMessage) -> MachineMessage:
        self.db.add(m); self.db.flush()
        return m

    def list_recent_for_connection(self, conn_id: int, limit: int = 100) -> list[MachineMessage]:
        return list(self.db.execute(
            select(MachineMessage)
            .where(MachineMessage.machine_connection_id == conn_id)
            .order_by(MachineMessage.id.desc())
            .limit(limit)
        ).scalars().all())
```

- [ ] **Step 5: 实现 service**

创建 `src/lightmes/modules/connectivity/service.py`：

```python
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.crypto import encrypt_password
from lightmes.modules.connectivity.models import (
    MachineConnection, MachineTopic, MqttConnection,
)
from lightmes.modules.connectivity.repository import (
    MachineConnectionRepository, MachineMessageRepository,
    MachineTopicRepository, MqttConnectionRepository,
)
from lightmes.modules.connectivity.schemas import ConnectionCreate, TopicCreate
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError

_VALID_FORMATS = {"json", "plain", "csv", "hex"}
_VALID_PROTOCOLS = {"mqtt"}  # V1 只支持 mqtt


class ConnectivityService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.conns = MachineConnectionRepository(db)
        self.mqtts = MqttConnectionRepository(db)
        self.topics = MachineTopicRepository(db)
        self.messages = MachineMessageRepository(db)

    # ---- Connection ----

    def create_connection(
        self,
        *,
        name: str,
        broker_host: str,
        broker_port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        keep_alive_seconds: int = 60,
        qos_default: int = 0,
        clean_session: bool = True,
        connect_timeout_seconds: int = 10,
        reconnect_delay_seconds: int = 5,
        description: str | None = None,
        protocol: str = "mqtt",
    ) -> tuple[MachineConnection, MqttConnection]:
        # 校验
        if protocol not in _VALID_PROTOCOLS:
            raise ValidationError(f"protocol 必须是 {sorted(_VALID_PROTOCOLS)} 之一: {protocol}")
        if not (1 <= broker_port <= 65535):
            raise ValidationError(f"broker_port 必须在 1-65535 之间: {broker_port}")
        if qos_default not in (0, 1, 2):
            raise ValidationError(f"qos_default 必须是 0/1/2: {qos_default}")
        # 重名检查
        if self.conns.get_by_name(name) is not None:
            raise BusinessRuleError(f"连接名称已存在: {name}")
        # 创建
        conn = self.conns.add(MachineConnection(
            name=name, description=description, protocol=protocol,
            is_active=False, status="disconnected"))
        mqtt = self.mqtts.add(MqttConnection(
            machine_connection_id=conn.id, broker_host=broker_host, broker_port=broker_port,
            username=username,
            password_encrypted=encrypt_password(password) if password else None,
            use_tls=use_tls, keep_alive_seconds=keep_alive_seconds,
            qos_default=qos_default, clean_session=clean_session,
            connect_timeout_seconds=connect_timeout_seconds,
            reconnect_delay_seconds=reconnect_delay_seconds))
        return conn, mqtt

    def get_connection(self, conn_id: int) -> MachineConnection:
        c = self.conns.get(conn_id)
        if c is None:
            raise NotFoundError(f"连接不存在: {conn_id}")
        return c

    def get_mqtt_for_connection(self, conn_id: int) -> MqttConnection | None:
        return self.mqtts.get_by_machine_connection(conn_id)

    def list_connections(self) -> list[MachineConnection]:
        return self.conns.list_all()

    def list_active_mqtt_connections(self) -> list[MachineConnection]:
        return self.conns.list_active()

    def activate_connection(self, conn_id: int) -> MachineConnection:
        c = self.get_connection(conn_id)
        c.is_active = True
        self.db.flush()
        return c

    def deactivate_connection(self, conn_id: int) -> MachineConnection:
        c = self.get_connection(conn_id)
        c.is_active = False
        self.db.flush()
        return c

    def delete_connection(self, conn_id: int) -> None:
        c = self.get_connection(conn_id)
        self.conns.delete(c.id)  # 级联删除 mqtt/topics/messages

    # ---- Topic ----

    def add_topic(
        self, conn_id: int, topic_pattern: str,
        payload_format: str = "json", description: str | None = None,
    ) -> MachineTopic:
        # 校验 connection 存在
        self.get_connection(conn_id)
        # 校验 format
        if payload_format not in _VALID_FORMATS:
            raise ValidationError(
                f"payload_format 必须是 {sorted(_VALID_FORMATS)} 之一: {payload_format}")
        # 重 pattern + active 检查
        if self.topics.find_active_duplicate(conn_id, topic_pattern) is not None:
            raise BusinessRuleError(
                f"同连接下已有相同的 active topic: {topic_pattern}（先停用旧的再加新）")
        return self.topics.add(MachineTopic(
            machine_connection_id=conn_id, topic_pattern=topic_pattern,
            payload_format=payload_format, description=description, is_active=True))

    def toggle_topic(self, conn_id: int, topic_id: int) -> MachineTopic:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在: {topic_id}")
        # 切换前若要激活，检查是否有同 pattern active 冲突
        if not t.is_active:
            existing = self.topics.find_active_duplicate(conn_id, t.topic_pattern)
            if existing is not None and existing.id != t.id:
                raise BusinessRuleError(
                    f"已有 active 的相同 pattern topic #{existing.id}，先停用之")
        t.is_active = not t.is_active
        self.db.flush()
        return t

    def delete_topic(self, conn_id: int, topic_id: int) -> None:
        t = self.topics.get(topic_id)
        if t is None or t.machine_connection_id != conn_id:
            raise NotFoundError(f"topic 不存在: {topic_id}")
        self.topics.delete(topic_id)

    def list_topics(self, conn_id: int) -> list[MachineTopic]:
        self.get_connection(conn_id)  # 校验存在
        return self.topics.list_for_connection(conn_id)

    # ---- Messages ----

    def list_recent_messages(self, conn_id: int, limit: int = 100) -> list:
        self.get_connection(conn_id)
        return self.messages.list_recent_for_connection(conn_id, limit)
```

- [ ] **Step 6: 运行 service 测试**

Run: `uv run pytest tests/modules/connectivity/test_service.py -v`
Expected: 13 tests PASS。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/connectivity/repository.py \
        src/lightmes/modules/connectivity/schemas.py \
        src/lightmes/modules/connectivity/service.py \
        tests/modules/connectivity/test_service.py
git commit -m "feat(connectivity): CRUD service + repositories + business validation"
```

---

### Task 3: Admin UI 列表 + 创建表单

**Files:**
- Create: `src/lightmes/modules/connectivity/router.py`
- Create: `src/lightmes/templates/connectivity/connections_list.html`
- Modify: `src/lightmes/modules/connectivity/__init__.py`（注册路由）
- Modify: `src/lightmes/templates/base.html`（加导航链接）
- Test: `tests/modules/connectivity/test_router.py`

**Interfaces:**
- Consumes: Task 2 的 `ConnectivityService`
- Produces:
  - `GET /connectivity` (重定向)
  - `GET /connectivity/connections` (列表 + 表单)
  - `POST /connectivity/connections` (创建)
  - `POST /connectivity/connections/{id}/activate` / `deactivate`
  - `POST /connectivity/connections/{id}/delete`

- [ ] **Step 1: 写失败测试 - router**

创建 `tests/modules/connectivity/test_router.py`：

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


def _login_admin(client, db_session, username="connadm"):
    AuthService(db_session).create_user(
        UserCreate(username=username, password="pw12345", display_name="Adm"))
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = db_session.query(User).filter(User.username == username).one()
    u.role_id = role.id
    db_session.flush()
    client.post("/login", data={"username": username, "password": "pw12345"})


def test_connectivity_index_redirects(client, db_session):
    _login_admin(client, db_session, "r1")
    resp = client.get("/connectivity", follow_redirects=False)
    assert resp.status_code in (301, 302, 303)
    assert "/connections" in resp.headers["location"]


def test_connections_list_requires_login(client, db_session):
    resp = client.get("/connectivity/connections", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_connections_list_renders_for_admin(client, db_session):
    _login_admin(client, db_session, "r2")
    resp = client.get("/connectivity/connections")
    assert resp.status_code == 200
    assert "数采连接" in resp.text or "MQTT" in resp.text


def test_connections_create_post(client, db_session):
    _login_admin(client, db_session, "r3")
    resp = client.post("/connectivity/connections", data={
        "name": "test-conn-list",
        "broker_host": "broker.local",
        "broker_port": "1883",
        "username": "user",
        "password": "pass",
    })
    assert resp.status_code in (200, 303)
    # 验证入库
    from lightmes.modules.connectivity.models import MachineConnection
    c = db_session.query(MachineConnection).filter(
        MachineConnection.name == "test-conn-list").one()
    assert c.mqtt_ref.broker_host == "broker.local" if hasattr(c, "mqtt_ref") else True
    # 直接查 mqtt_connections
    from lightmes.modules.connectivity.models import MqttConnection
    m = db_session.query(MqttConnection).filter(
        MqttConnection.machine_connection_id == c.id).one()
    assert m.broker_host == "broker.local"


def test_connections_activate_toggle(client, db_session):
    _login_admin(client, db_session, "r4")
    # 先 create
    client.post("/connectivity/connections", data={
        "name": "act-conn", "broker_host": "x", "broker_port": "1883"})
    from lightmes.modules.connectivity.models import MachineConnection
    c = db_session.query(MachineConnection).filter(
        MachineConnection.name == "act-conn").one()
    resp = client.post(f"/connectivity/connections/{c.id}/activate")
    assert resp.status_code in (200, 303)
    db_session.refresh(c)
    assert c.is_active is True
    # deactivate
    resp = client.post(f"/connectivity/connections/{c.id}/deactivate")
    db_session.refresh(c)
    assert c.is_active is False


def test_connections_delete(client, db_session):
    _login_admin(client, db_session, "r5")
    client.post("/connectivity/connections", data={
        "name": "del-conn", "broker_host": "x", "broker_port": "1883"})
    from lightmes.modules.connectivity.models import MachineConnection
    c = db_session.query(MachineConnection).filter(
        MachineConnection.name == "del-conn").one()
    resp = client.post(f"/connectivity/connections/{c.id}/delete")
    assert resp.status_code in (200, 303)
    assert db_session.get(MachineConnection, c.id) is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/connectivity/test_router.py -v`
Expected: 404（路由不存在）。

- [ ] **Step 3: 实现 router**

创建 `src/lightmes/modules/connectivity/router.py`：

```python
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_role
from lightmes.modules.auth.models import User
from lightmes.modules.connectivity.service import ConnectivityService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/connectivity", response_class=HTMLResponse)
def connectivity_index(request: Request) -> HTMLResponse:
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.get("/connectivity/connections", response_class=HTMLResponse)
def connections_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    connections = svc.list_connections()
    # 附加 mqtt 信息
    conn_views = []
    for c in connections:
        mqtt = svc.get_mqtt_for_connection(c.id)
        conn_views.append({
            "id": c.id, "name": c.name, "description": c.description,
            "is_active": c.is_active, "status": c.status,
            "status_message": c.status_message,
            "messages_received": c.messages_received,
            "last_connected_at": c.last_connected_at,
            "broker_host": mqtt.broker_host if mqtt else "—",
            "broker_port": mqtt.broker_port if mqtt else "—",
        })
    return templates.TemplateResponse("connectivity/connections_list.html", {
        "request": request, "connections": conn_views,
    })


@router.post("/connectivity/connections", response_class=HTMLResponse)
def connections_create(
    request: Request,
    name: str = Form(...),
    broker_host: str = Form(...),
    broker_port: int = Form(1883),
    username: str | None = Form(None),
    password: str | None = Form(None),
    use_tls: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.create_connection(
            name=name, broker_host=broker_host, broker_port=broker_port,
            username=username or None, password=password or None,
            use_tls=bool(use_tls))
        db.commit()
    except DomainError as e:
        return HTMLResponse(f"创建失败: {e.detail}", status_code=400)
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.post("/connectivity/connections/{conn_id}/activate", response_class=HTMLResponse)
def connections_activate(
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.activate_connection(conn_id)
    db.commit()
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.post("/connectivity/connections/{conn_id}/deactivate", response_class=HTMLResponse)
def connections_deactivate(
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.deactivate_connection(conn_id)
    db.commit()
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.post("/connectivity/connections/{conn_id}/delete", response_class=HTMLResponse)
def connections_delete(
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.delete_connection(conn_id)
    db.commit()
    return RedirectResponse(url="/connectivity/connections", status_code=303)
```

注意：`use_tls: bool = Form(False)` 对于 checkbox 表单字段需要特殊处理 —— checkbox 未勾选时浏览器不会发送该字段。FastAPI/Pydantic 会用默认 False，正好匹配需求。

- [ ] **Step 4: 创建 connections_list.html 模板**

创建 `src/lightmes/templates/connectivity/connections_list.html`：

```html
{% extends "base.html" %}
{% block title %}数采连接{% endblock %}
{% block content %}
<h1 class="page-title">数采连接 <small>MQTT</small></h1>

<div class="card">
  <div class="card__title">新建 MQTT 连接</div>
  <form method="post" action="/connectivity/connections" class="form-row">
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field"><label>Broker Host</label><input name="broker_host" placeholder="192.168.1.10" required></div>
    <div class="field" style="max-width:100px"><label>Port</label><input name="broker_port" type="number" value="1883"></div>
    <div class="field"><label>Username</label><input name="username"></div>
    <div class="field"><label>Password</label><input name="password" type="password"></div>
    <div class="field"><label>Use TLS</label><input type="checkbox" name="use_tls" value="true"></div>
    <button type="submit">创建</button>
  </form>
</div>

<div class="card">
  <div class="card__title">连接列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>名称</th><th>Broker</th><th>状态</th><th>消息数</th><th>最后连接</th><th>操作</th></tr></thead>
    <tbody>
      {% for c in connections %}
      <tr>
        <td>{{ c.id }}</td>
        <td><a href="/connectivity/connections/{{ c.id }}">{{ c.name }}</a></td>
        <td><code>{{ c.broker_host }}:{{ c.broker_port }}</code></td>
        <td>
          {% if c.status == 'connected' %}<span class="badge badge--ok">connected</span>
          {% elif c.status == 'connecting' %}<span class="badge">connecting</span>
          {% elif c.status == 'error' %}<span class="badge badge--danger" title="{{ c.status_message }}">error</span>
          {% else %}<span class="badge">disconnected</span>{% endif %}
        </td>
        <td>{{ c.messages_received }}</td>
        <td>{{ c.last_connected_at.strftime('%Y-%m-%d %H:%M') if c.last_connected_at else '—' }}</td>
        <td>
          {% if c.is_active %}
          <form method="post" action="/connectivity/connections/{{ c.id }}/deactivate" style="display:inline">
            <button type="submit">停用</button>
          </form>
          {% else %}
          <form method="post" action="/connectivity/connections/{{ c.id }}/activate" style="display:inline">
            <button type="submit">启用</button>
          </form>
          {% endif %}
          <form method="post" action="/connectivity/connections/{{ c.id }}/delete" style="display:inline"
                onsubmit="return confirm('确认删除连接 {{ c.name }}？相关 topic 和消息会一并删除。')">
            <button type="submit" class="btn-danger">删除</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" style="color:#6b7280;text-align:center">暂无连接</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: 注册路由到 connectivity/__init__.py**

修改 `src/lightmes/modules/connectivity/__init__.py`：

```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Register connectivity admin routes."""
    from lightmes.modules.connectivity import models  # noqa: F401
    from lightmes.modules.connectivity.router import router
    app.include_router(router)
```

- [ ] **Step 6: 加导航链接到 base.html**

修改 `src/lightmes/templates/base.html`，在 `API Keys` 链接后追加：

```html
    <a class="app-bar__link" href="/connectivity/connections">数采</a>
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/connectivity/test_router.py -v`
Expected: 6 tests PASS。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/connectivity/router.py \
        src/lightmes/modules/connectivity/__init__.py \
        src/lightmes/templates/connectivity/connections_list.html \
        src/lightmes/templates/base.html \
        tests/modules/connectivity/test_router.py
git commit -m "feat(connectivity): admin UI for connection list + create/activate/delete"
```

---

### Task 4: Admin UI 详情页 + Topic CRUD

**Files:**
- Modify: `src/lightmes/modules/connectivity/router.py`（追加详情路由 + topic 路由）
- Create: `src/lightmes/templates/connectivity/connection_detail.html`
- Test: `tests/modules/connectivity/test_router.py`（扩展）

**Interfaces:**
- Consumes: Task 2-3 的 service
- Produces:
  - `GET /connectivity/connections/{id}` (详情 + Topic + 最近消息)
  - `POST /connectivity/connections/{id}/topics` (添加 topic)
  - `POST /connectivity/connections/{id}/topics/{tid}/toggle`
  - `POST /connectivity/connections/{id}/topics/{tid}/delete`

- [ ] **Step 1: 写失败测试 - detail + topic CRUD**

在 `tests/modules/connectivity/test_router.py` 末尾追加：

```python
def _make_conn(db_session, name="t-detail"):
    from lightmes.modules.connectivity.models import MachineConnection, MqttConnection
    c = MachineConnection(name=name)
    db_session.add(c); db_session.flush()
    m = MqttConnection(machine_connection_id=c.id, broker_host="x", broker_port=1883)
    db_session.add(m); db_session.flush()
    return c


def test_connection_detail_renders(client, db_session):
    _login_admin(client, db_session, "d1")
    c = _make_conn(db_session, "detail-r")
    resp = client.get(f"/connectivity/connections/{c.id}")
    assert resp.status_code == 200
    assert "detail-r" in resp.text


def test_connection_detail_not_found(client, db_session):
    _login_admin(client, db_session, "d2")
    resp = client.get("/connectivity/connections/99999")
    assert resp.status_code == 404


def test_topic_add_via_post(client, db_session):
    _login_admin(client, db_session, "d3")
    c = _make_conn(db_session, "topic-add")
    resp = client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/+/count",
        "payload_format": "json",
    })
    assert resp.status_code in (200, 303)
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    assert t.topic_pattern == "machine/+/count"


def test_topic_toggle_via_post(client, db_session):
    _login_admin(client, db_session, "d4")
    c = _make_conn(db_session, "topic-tog")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    assert t.is_active is True
    # toggle → False
    resp = client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/toggle")
    assert resp.status_code in (200, 303)
    db_session.refresh(t)
    assert t.is_active is False


def test_topic_delete_via_post(client, db_session):
    _login_admin(client, db_session, "d5")
    c = _make_conn(db_session, "topic-del")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    resp = client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/delete")
    assert resp.status_code in (200, 303)
    assert db_session.get(MachineTopic, t.id) is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/connectivity/test_router.py -v -k "detail or topic"`
Expected: 404。

- [ ] **Step 3: 追加详情路由**

在 `src/lightmes/modules/connectivity/router.py` 末尾追加：

```python
@router.get("/connectivity/connections/{conn_id}", response_class=HTMLResponse)
def connection_detail(
    request: Request,
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    from lightmes.shared.errors import NotFoundError
    svc = ConnectivityService(db)
    try:
        conn = svc.get_connection(conn_id)
    except NotFoundError:
        return HTMLResponse("连接不存在", status_code=404)
    mqtt = svc.get_mqtt_for_connection(conn_id)
    topics = svc.list_topics(conn_id)
    messages = svc.list_recent_messages(conn_id, limit=100)
    return templates.TemplateResponse("connectivity/connection_detail.html", {
        "request": request,
        "conn": {
            "id": conn.id, "name": conn.name, "description": conn.description,
            "is_active": conn.is_active, "status": conn.status,
            "status_message": conn.status_message,
            "messages_received": conn.messages_received,
            "last_connected_at": conn.last_connected_at,
            "broker_host": mqtt.broker_host if mqtt else "—",
            "broker_port": mqtt.broker_port if mqtt else "—",
            "username": mqtt.username if mqtt else None,
            "use_tls": mqtt.use_tls if mqtt else False,
            "qos_default": mqtt.qos_default if mqtt else 0,
        },
        "topics": topics,
        "messages": messages,
    })


@router.post("/connectivity/connections/{conn_id}/topics", response_class=HTMLResponse)
def topic_add(
    request: Request,
    conn_id: int,
    topic_pattern: str = Form(...),
    payload_format: str = Form("json"),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.add_topic(conn_id, topic_pattern, payload_format, description or None)
        db.commit()
    except DomainError as e:
        return HTMLResponse(f"添加失败: {e.detail}", status_code=400)
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/toggle",
             response_class=HTMLResponse)
def topic_toggle(
    conn_id: int,
    topic_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.toggle_topic(conn_id, topic_id)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/delete",
             response_class=HTMLResponse)
def topic_delete(
    conn_id: int,
    topic_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.delete_topic(conn_id, topic_id)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)
```

- [ ] **Step 4: 创建 connection_detail.html**

创建 `src/lightmes/templates/connectivity/connection_detail.html`：

```html
{% extends "base.html" %}
{% block title %}{{ conn.name }} — 数采连接{% endblock %}
{% block content %}
<h1 class="page-title">{{ conn.name }} <small>{{ conn.broker_host }}:{{ conn.broker_port }}</small></h1>

<div class="card">
  <div class="card__title">基本信息</div>
  <table class="data-table">
    <tr><th>状态</th><td>
      {% if conn.status == 'connected' %}<span class="badge badge--ok">connected</span>
      {% elif conn.status == 'connecting' %}<span class="badge">connecting</span>
      {% elif conn.status == 'error' %}<span class="badge badge--danger">error: {{ conn.status_message }}</span>
      {% else %}<span class="badge">disconnected</span>{% endif %}
    </td></tr>
    <tr><th>启用状态</th><td>{{ '启用' if conn.is_active else '停用' }}</td></tr>
    <tr><th>消息数</th><td>{{ conn.messages_received }}</td></tr>
    <tr><th>最后连接</th><td>{{ conn.last_connected_at.strftime('%Y-%m-%d %H:%M:%S') if conn.last_connected_at else '—' }}</td></tr>
    <tr><th>Username</th><td>{{ conn.username or '—' }}</td></tr>
    <tr><th>TLS</th><td>{{ '是' if conn.use_tls else '否' }}</td></tr>
    <tr><th>QoS</th><td>{{ conn.qos_default }}</td></tr>
    <tr><th>描述</th><td>{{ conn.description or '—' }}</td></tr>
  </table>
  <p style="margin-top:12px">
    <a href="/connectivity/connections">← 返回列表</a>
    {% if conn.is_active %}
    <form method="post" action="/connectivity/connections/{{ conn.id }}/deactivate" style="display:inline">
      <button type="submit">停用连接</button>
    </form>
    {% else %}
    <form method="post" action="/connectivity/connections/{{ conn.id }}/activate" style="display:inline">
      <button type="submit">启用连接</button>
    </form>
    {% endif %}
  </p>
</div>

<div class="card">
  <div class="card__title">订阅主题（{{ topics|length }}）</div>
  <form method="post" action="/connectivity/connections/{{ conn.id }}/topics" class="form-row">
    <div class="field" style="flex:2"><label>Topic 模式</label>
      <input name="topic_pattern" placeholder="machine/line1/+/count" required></div>
    <div class="field"><label>格式</label>
      <select name="payload_format">
        <option value="json">json</option>
        <option value="plain">plain</option>
        <option value="csv">csv</option>
        <option value="hex">hex</option>
      </select>
    </div>
    <div class="field"><label>描述</label><input name="description"></div>
    <button type="submit">添加</button>
  </form>
  <table class="data-table">
    <thead><tr><th>ID</th><th>模式</th><th>格式</th><th>描述</th><th>状态</th><th>操作</th></tr></thead>
    <tbody>
      {% for t in topics %}
      <tr>
        <td>{{ t.id }}</td>
        <td><code>{{ t.topic_pattern }}</code></td>
        <td>{{ t.payload_format }}</td>
        <td>{{ t.description or '—' }}</td>
        <td>{% if t.is_active %}<span class="badge badge--ok">active</span>{% else %}<span class="badge">disabled</span>{% endif %}</td>
        <td>
          <form method="post" action="/connectivity/connections/{{ conn.id }}/topics/{{ t.id }}/toggle" style="display:inline">
            <button type="submit">{% if t.is_active %}停用{% else %}启用{% endif %}</button>
          </form>
          <form method="post" action="/connectivity/connections/{{ conn.id }}/topics/{{ t.id }}/delete" style="display:inline"
                onsubmit="return confirm('确认删除 topic {{ t.topic_pattern }}？')">
            <button type="submit" class="btn-danger">删除</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" style="color:#6b7280;text-align:center">暂无 topic</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="card">
  <div class="card__title">最近消息（{{ messages|length }}/100）</div>
  <table class="data-table">
    <thead><tr><th>时间</th><th>Topic</th><th>Payload</th><th>匹配</th></tr></thead>
    <tbody>
      {% for m in messages %}
      <tr>
        <td>{{ m.received_at.strftime('%H:%M:%S') }}</td>
        <td><code style="word-break:break-all">{{ m.topic }}</code></td>
        <td><pre style="max-width:600px;white-space:pre-wrap;max-height:80px;overflow:auto">{{ m.raw_payload[:200] }}{% if m.raw_payload|length > 200 %}...{% endif %}</pre></td>
        <td>
          {% if m.matched_topic_id %}<a href="#topic-{{ m.matched_topic_id }}">#{{ m.matched_topic_id }}</a>
          {% else %}<span class="badge">skipped</span>{% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" style="color:#6b7280;text-align:center">暂无消息</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/connectivity/test_router.py -v`
Expected: 11 tests PASS（6 from Task 3 + 5 new）。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/connectivity/router.py \
        src/lightmes/templates/connectivity/connection_detail.html \
        tests/modules/connectivity/test_router.py
git commit -m "feat(connectivity): connection detail page + Topic CRUD + recent messages view"
```

---

### Task 5: Listener supervisor + reconcile + MessageService

**Files:**
- Create: `src/lightmes/modules/connectivity/mqtt_listener/__init__.py`
- Create: `src/lightmes/modules/connectivity/mqtt_listener/supervisor.py`
- Create: `src/lightmes/modules/connectivity/mqtt_listener/message_service.py`（消息入库逻辑）
- Test: `tests/modules/connectivity/test_message_service.py`

**Interfaces:**
- Consumes: Task 1 的 `matches_topic` + Task 2 的 `MachineTopic` 等
- Produces:
  - `persist_message(connection_id, topic, payload, received_at)` 同步函数，独立 SessionLocal，递增 messages_received
  - `MessagePersistResult` dataclass（status, matched_topic_id, error）

- [ ] **Step 1: 写失败测试 - message_service**

创建 `tests/modules/connectivity/test_message_service.py`：

```python
from datetime import datetime, timezone
from lightmes.modules.connectivity.models import (
    MachineConnection, MachineTopic, MachineMessage,
)
from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message


def _conn(db_session, name="msg-test"):
    c = MachineConnection(name=name, is_active=True)
    db_session.add(c); db_session.flush()
    return c


def test_persist_message_with_matching_topic(db_session):
    c = _conn(db_session, "match")
    t = MachineTopic(
        machine_connection_id=c.id, topic_pattern="machine/+/count",
        payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    result = persist_message(
        c.id, "machine/L1/count", b'{"count": 1}',
        datetime.now(timezone.utc))
    assert result.status == "ok"
    assert result.matched_topic_id == t.id


def test_persist_message_no_match_status_skipped(db_session):
    c = _conn(db_session, "no-match")
    # 加一个不匹配的 topic
    t = MachineTopic(
        machine_connection_id=c.id, topic_pattern="machine/other",
        payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    result = persist_message(
        c.id, "machine/L1/count", b'{"count": 1}',
        datetime.now(timezone.utc))
    assert result.status == "skipped"
    assert result.matched_topic_id is None


def test_persist_message_increments_count(db_session):
    from sqlalchemy import select
    from lightmes.database import SessionLocal
    from lightmes.modules.connectivity.models import MachineConnection as MC

    c = _conn(db_session, "count")
    db_session.commit()  # 让独立 session 看到这条

    persist_message(c.id, "machine/x", b"x", datetime.now(timezone.utc))
    persist_message(c.id, "machine/x", b"y", datetime.now(timezone.utc))

    db = SessionLocal()
    try:
        conn = db.get(MC, c.id)
        assert conn.messages_received == 2
        msgs = list(db.execute(
            select(MachineMessage).where(MachineMessage.machine_connection_id == c.id)
        ).scalars().all())
        assert len(msgs) == 2
    finally:
        db.close()


def test_persist_message_invalid_utf8_payload(db_session):
    """二进制 payload（非 UTF-8）应该用 replace 策略，不抛异常。"""
    c = _conn(db_session, "binary")
    db_session.commit()
    result = persist_message(
        c.id, "machine/binary", b"\xff\xfe\x00binary",
        datetime.now(timezone.utc))
    assert result.status in ("ok", "skipped")
    assert result.error is None


def test_persist_message_handles_db_failure_gracefully(db_session):
    """connection_id 不存在 → 持久化失败但函数不抛异常。"""
    result = persist_message(
        99999, "machine/x", b"y", datetime.now(timezone.utc))
    assert result.status == "error"
    assert result.error is not None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/connectivity/test_message_service.py -v`
Expected: ImportError on `message_service`。

- [ ] **Step 3: 实现 message_service**

创建 `src/lightmes/modules/connectivity/mqtt_listener/__init__.py`（空）+ `message_service.py`：

```python
"""Message persistence logic — invoked by MQTT client task on each received message.

Uses independent SessionLocal to avoid polluting any request-scoped session.
Never raises — failures captured as result.error.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update

from lightmes.database import SessionLocal
from lightmes.modules.connectivity.models import (
    MachineConnection, MachineMessage, MachineTopic,
)
from lightmes.modules.connectivity.topic_match import matches_topic


@dataclass
class MessagePersistResult:
    status: str  # "ok" | "skipped" | "error"
    matched_topic_id: int | None = None
    error: str | None = None


def persist_message(
    connection_id: int,
    topic: str,
    payload: bytes,
    received_at: datetime,
) -> MessagePersistResult:
    """Persist one received MQTT message. Returns result; never raises."""
    db = SessionLocal()
    try:
        # 1. 校验 connection 存在
        conn = db.get(MachineConnection, connection_id)
        if conn is None:
            return MessagePersistResult(status="error", error=f"connection 不存在: {connection_id}")
        # 2. 查 active topics
        topics = list(db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == connection_id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all())
        # 3. 找匹配
        matched = next((t for t in topics if matches_topic(t.topic_pattern, topic)), None)
        # 4. 入库
        msg = MachineMessage(
            machine_connection_id=connection_id,
            topic=topic,
            raw_payload=payload.decode("utf-8", errors="replace"),
            matched_topic_id=matched.id if matched else None,
            processing_status="ok" if matched else "skipped",
            received_at=received_at,
        )
        db.add(msg)
        # 5. 递增计数器（UPDATE，避免 race condition）
        db.execute(
            update(MachineConnection)
            .where(MachineConnection.id == connection_id)
            .values(messages_received=MachineConnection.messages_received + 1)
        )
        db.commit()
        return MessagePersistResult(
            status="ok" if matched else "skipped",
            matched_topic_id=matched.id if matched else None,
        )
    except Exception as e:
        db.rollback()
        return MessagePersistResult(status="error", error=str(e))
    finally:
        db.close()
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/connectivity/test_message_service.py -v`
Expected: 5 tests PASS。

- [ ] **Step 5: 实现 supervisor 雏形（不含真实 MQTT，先 reconcile 骨架）**

创建 `src/lightmes/modules/connectivity/mqtt_listener/supervisor.py`：

```python
"""MQTT listener supervisor — reconciles DB active connections with managed client tasks.

This module is the single entry point for the listener process:
    python -m lightmes.connectivity.mqtt_listener

The supervisor periodically (every RECONCILE_SECONDS) queries the DB for active
MQTT connections and:
    - spawns a client task for newly-active connections
    - cancels client tasks for deactivated/deleted connections
    - restarts client tasks whose config (broker host/port/topics/etc.) changed

Each connection runs as an independent asyncio task via run_client_with_reconnect()
from lightmes.modules.connectivity.mqtt_listener.client.
"""
import asyncio
import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import SessionLocal
from lightmes.modules.connectivity.crypto import decrypt_password
from lightmes.modules.connectivity.models import (
    MachineConnection, MachineTopic, MqttConnection,
)

logger = logging.getLogger(__name__)

RECONCILE_SECONDS = 5


@dataclass
class ResolvedConnectionConfig:
    """Decrypted, ready-to-use MQTT config for one connection."""
    connection_id: int
    broker_host: str
    broker_port: int
    client_id: str
    username: str | None
    password: str | None
    use_tls: bool
    keep_alive_seconds: int
    qos_default: int
    clean_session: bool
    connect_timeout_seconds: int
    reconnect_delay_seconds: int
    topics: list[MachineTopic]


def resolve_config(db: Session, conn: MachineConnection) -> ResolvedConnectionConfig | None:
    """Resolve a MachineConnection + its MqttConnection into a usable config.

    Returns None if the connection has no mqtt_connections row (data inconsistency).
    """
    mqtt = db.execute(
        select(MqttConnection).where(MqttConnection.machine_connection_id == conn.id)
    ).scalar_one_or_none()
    if mqtt is None:
        return None
    topics = list(db.execute(
        select(MachineTopic).where(
            MachineTopic.machine_connection_id == conn.id,
            MachineTopic.is_active.is_(True),
        )
    ).scalars().all())
    # 自动生成 client_id if 不设
    client_id = mqtt.client_id or f"lightmes-{conn.id}-{hashlib.md5(f'{conn.id}'.encode()).hexdigest()[:8]}"
    return ResolvedConnectionConfig(
        connection_id=conn.id,
        broker_host=mqtt.broker_host,
        broker_port=mqtt.broker_port,
        client_id=client_id,
        username=mqtt.username,
        password=decrypt_password(mqtt.password_encrypted),
        use_tls=mqtt.use_tls,
        keep_alive_seconds=mqtt.keep_alive_seconds,
        qos_default=mqtt.qos_default,
        clean_session=mqtt.clean_session,
        connect_timeout_seconds=mqtt.connect_timeout_seconds,
        reconnect_delay_seconds=mqtt.reconnect_delay_seconds,
        topics=topics,
    )


def compute_config_signature(config: ResolvedConnectionConfig) -> str:
    """Hash the connection-affecting fields. Change in sig → reconnect needed."""
    payload = (
        f"{config.broker_host}:{config.broker_port}|"
        f"{config.username}|{config.use_tls}|{config.qos_default}|"
        f"{config.keep_alive_seconds}|{config.clean_session}|"
        f"{config.client_id}|"
        f"{'|'.join(sorted(t.topic_pattern for t in config.topics))}"
    )
    return hashlib.md5(payload.encode()).hexdigest()


def fetch_active_configs(db: Session) -> list[tuple[MachineConnection, ResolvedConnectionConfig | None]]:
    """Fetch all active MQTT connections with their resolved configs."""
    conns = list(db.execute(
        select(MachineConnection).where(
            MachineConnection.is_active.is_(True),
            MachineConnection.protocol == "mqtt",
        )
    ).scalars().all())
    return [(c, resolve_config(db, c)) for c in conns]


def reconcile(
    managed: dict[int, asyncio.Task],
    sigs: dict[int, str],
    spawn_fn,
    cancel_fn,
) -> None:
    """Compare DB active connections to in-memory managed tasks.

    spawn_fn(config) is called for new connections (or when config sig changed).
    cancel_fn(connection_id) is called for removed connections.

    This is a pure function (no I/O) — caller provides spawn_fn/cancel_fn.
    Tested independently of asyncio.
    """
    db = SessionLocal()
    try:
        active = fetch_active_configs(db)
    finally:
        db.close()

    active_ids = {c.id for c, _ in active}
    managed_ids = set(managed.keys())

    # 新增 / 变更 → spawn
    for c, config in active:
        if config is None:
            continue
        new_sig = compute_config_signature(config)
        if c.id not in managed_ids:
            spawn_fn(config)
            sigs[c.id] = new_sig
        elif sigs.get(c.id) != new_sig:
            cancel_fn(c.id)
            # 下次 reconcile 时会重新 spawn（因为 sigs 没更新 + managed 已删）
            # 简化：立即 spawn 新配置
            spawn_fn(config)
            sigs[c.id] = new_sig

    # 删除 → cancel
    for cid in managed_ids - active_ids:
        cancel_fn(cid)
```

- [ ] **Step 6: 写 supervisor 单元测试（用 mock spawn/cancel）**

在 `tests/modules/connectivity/test_message_service.py` 末尾追加（或新建 test_supervisor.py）：

创建 `tests/modules/connectivity/test_supervisor.py`：

```python
from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    reconcile, compute_config_signature, ResolvedConnectionConfig,
)
from lightmes.modules.connectivity.models import MachineConnection, MachineTopic


def _config(conn_id=1, host="x", port=1883, topics=None):
    return ResolvedConnectionConfig(
        connection_id=conn_id, broker_host=host, broker_port=port,
        client_id="c1", username=None, password=None, use_tls=False,
        keep_alive_seconds=60, qos_default=0, clean_session=True,
        connect_timeout_seconds=10, reconnect_delay_seconds=5,
        topics=topics or [])


def test_compute_signature_stable():
    a = _config()
    b = _config()
    assert compute_config_signature(a) == compute_config_signature(b)


def test_compute_signature_changes_with_host():
    a = _config(host="x")
    b = _config(host="y")
    assert compute_config_signature(a) != compute_config_signature(b)


def test_compute_signature_changes_with_topics():
    t = MachineTopic(id=1, machine_connection_id=1, topic_pattern="x", is_active=True)
    a = _config(topics=[])
    b = _config(topics=[t])
    assert compute_config_signature(a) != compute_config_signature(b)


def test_reconcile_spawns_new_connection(db_session):
    """新建 active connection → spawn_fn 被调用。"""
    c = MachineConnection(name="rec-1", is_active=True, protocol="mqtt")
    db_session.add(c); db_session.flush()
    from lightmes.modules.connectivity.models import MqttConnection
    db_session.add(MqttConnection(machine_connection_id=c.id, broker_host="x", broker_port=1883))
    db_session.commit()

    spawned = []
    cancelled = []
    managed = {}
    sigs = {}
    reconcile(managed, sigs, spawn_fn=lambda cfg: spawned.append(cfg), cancel_fn=lambda cid: cancelled.append(cid))
    assert len(spawned) == 1
    assert spawned[0].connection_id == c.id
    assert len(cancelled) == 0


def test_reconcile_cancels_removed_connection(db_session):
    """已 managed 的 connection 在 DB 中变成 inactive → cancel_fn 被调用。"""
    c = MachineConnection(name="rec-2", is_active=True, protocol="mqtt")
    db_session.add(c); db_session.flush()
    from lightmes.modules.connectivity.models import MqttConnection
    db_session.add(MqttConnection(machine_connection_id=c.id, broker_host="x", broker_port=1883))
    db_session.commit()

    spawned = []
    cancelled = []
    managed = {}
    sigs = {}
    # 第一次：spawn
    reconcile(managed, sigs, lambda cfg: spawned.append(cfg), lambda cid: cancelled.append(cid))
    # 停用
    c.is_active = False
    db_session.commit()
    spawned.clear(); cancelled.clear()
    # 第二次：cancel
    reconcile(managed, sigs, lambda cfg: spawned.append(cfg), lambda cid: cancelled.append(cid))
    assert len(cancelled) == 1
    assert cancelled[0] == c.id
```

- [ ] **Step 7: 运行 supervisor 测试**

Run: `uv run pytest tests/modules/connectivity/test_supervisor.py -v`
Expected: 5 tests PASS。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/connectivity/mqtt_listener/__init__.py \
        src/lightmes/modules/connectivity/mqtt_listener/message_service.py \
        src/lightmes/modules/connectivity/mqtt_listener/supervisor.py \
        tests/modules/connectivity/test_message_service.py \
        tests/modules/connectivity/test_supervisor.py
git commit -m "feat(connectivity): message persist service + supervisor reconcile (no MQTT yet)"
```

---

### Task 6: Listener client task + 重连 + __main__ entry

**Files:**
- Create: `src/lightmes/modules/connectivity/mqtt_listener/client.py`（async client with aiomqtt）
- Create: `src/lightmes/modules/connectivity/mqtt_listener/__main__.py`（CLI entry）
- Modify: `src/lightmes/modules/connectivity/mqtt_listener/supervisor.py`（接 main loop）
- Test: 集成测试默认 skip（需真实 broker）

**Interfaces:**
- Consumes: Task 5 的 `persist_message` + supervisor + `ResolvedConnectionConfig`
- Produces:
  - `run_client_with_reconnect(config: ResolvedConnectionConfig, stop_event: asyncio.Event)` async function
  - `mark_status(connection_id, status, message=None, last_connected_at=None)` 同步函数
  - `async def main()` 在 `__main__.py`：组装 supervisor loop + SIGTERM handler

- [ ] **Step 1: 实现 mark_status helper**

在 `src/lightmes/modules/connectivity/mqtt_listener/supervisor.py` 末尾追加：

```python
def mark_status(
    connection_id: int,
    status: str,
    message: str | None = None,
    last_connected_at=None,
) -> None:
    """Update MachineConnection.status. Independent session. Never raises."""
    from datetime import datetime
    db = SessionLocal()
    try:
        conn = db.get(MachineConnection, connection_id)
        if conn is None:
            return
        conn.status = status
        if message is not None:
            conn.status_message = message[:500]
        elif status == "connected":
            conn.status_message = None  # clear on success
        if last_connected_at is not None:
            conn.last_connected_at = last_connected_at
        elif status == "connected":
            conn.last_connected_at = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"mark_status failed conn={connection_id} status={status}: {e}")
    finally:
        db.close()
```

- [ ] **Step 2: 实现 client.py**

创建 `src/lightmes/modules/connectivity/mqtt_listener/client.py`：

```python
"""Async MQTT client task for one connection — connect, subscribe, receive, reconnect."""
import asyncio
import logging
from datetime import datetime, timezone

from aiomqtt import Client, MqttError

from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message
from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    ResolvedConnectionConfig, mark_status,
)

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300  # 5 min cap


async def run_client_with_reconnect(
    config: ResolvedConnectionConfig,
    stop_event: asyncio.Event,
) -> None:
    """Connect → subscribe → receive → on failure → backoff → retry.

    Exits cleanly when stop_event is set (supervisor cancelled this task).
    """
    backoff = config.reconnect_delay_seconds
    while not stop_event.is_set():
        try:
            mark_status(config.connection_id, "connecting")
            # Build TLS context if needed
            tls_params = None
            if config.use_tls:
                import ssl
                tls_params = ssl.create_default_context()

            client_kwargs = dict(
                hostname=config.broker_host,
                port=config.broker_port,
                identifier=config.client_id,
                keepalive=config.keep_alive_seconds,
                timeout=config.connect_timeout_seconds,
                clean_session=config.clean_session,
            )
            if config.username:
                client_kwargs["username"] = config.username
            if config.password:
                client_kwargs["password"] = config.password
            if tls_params:
                client_kwargs["tls_context"] = tls_params

            async with Client(**client_kwargs) as client:
                # Subscribe to all active topics
                for t in config.topics:
                    await client.subscribe(t.topic_pattern, qos=config.qos_default)
                logger.info(
                    f"[conn {config.connection_id}] connected to "
                    f"{config.broker_host}:{config.broker_port}, "
                    f"subscribed to {len(config.topics)} topics")
                mark_status(config.connection_id, "connected")
                # Receive loop
                async for message in client.messages:
                    if stop_event.is_set():
                        break
                    try:
                        result = persist_message(
                            config.connection_id,
                            str(message.topic),
                            bytes(message.payload),
                            datetime.now(timezone.utc),
                        )
                        if result.status == "error":
                            logger.warning(
                                f"[conn {config.connection_id}] persist failed: "
                                f"topic={message.topic} err={result.error}")
                    except Exception as e:
                        # persist_message 不应抛，但二次防御
                        logger.exception(
                            f"[conn {config.connection_id}] unexpected error in "
                            f"persist_message: {e}")
                # Broker disconnected cleanly
            mark_status(config.connection_id, "disconnected")
            return
        except asyncio.CancelledError:
            # Supervisor cancelled us
            mark_status(config.connection_id, "disconnected")
            raise
        except MqttError as e:
            mark_status(config.connection_id, "error", message=str(e))
            logger.warning(
                f"[conn {config.connection_id}] MQTT error: {e}; "
                f"reconnect in {backoff}s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return  # stop signaled
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        except Exception as e:
            mark_status(config.connection_id, "error", message=str(e))
            logger.exception(
                f"[conn {config.connection_id}] unexpected crash: {e}; "
                f"reconnect in {backoff}s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
```

- [ ] **Step 3: 实现 __main__.py**

创建 `src/lightmes/modules/connectivity/mqtt_listener/__main__.py`：

```python
"""CLI entry: python -m lightmes.connectivity.mqtt_listener

Long-running supervisor process. Reads active MQTT connections from DB
every RECONCILE_SECONDS seconds, spawns client tasks, cancels them on
deactivation/delete, restarts on config change.

Signals: SIGTERM/SIGINT → graceful shutdown (cancel all client tasks).
"""
import asyncio
import logging
import signal
import sys

from lightmes.modules.connectivity.mqtt_listener.supervisor import (
    RECONCILE_SECONDS, reconcile, fetch_active_configs, compute_config_signature,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("lightmes.connectivity.mqtt_listener")


async def main() -> int:
    logger.info("MQTT listener starting")
    stop_event = asyncio.Event()
    # Signal handler (only works on main thread)
    def _signal_handler(*_):
        logger.info("Signal received, shutting down...")
        stop_event.set()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except NotImplementedError:
        # Windows: add_signal_handler not supported
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    managed: dict[int, asyncio.Task] = {}
    sigs: dict[int, str] = {}

    async def spawn(config):
        if config.connection_id in managed and not managed[config.connection_id].done():
            managed[config.connection_id].cancel()
        # Lazy import (avoid pulling aiomqtt when only --help)
        from lightmes.modules.connectivity.mqtt_listener.client import run_client_with_reconnect
        task = asyncio.create_task(
            run_client_with_reconnect(config, stop_event),
            name=f"mqtt-client-{config.connection_id}",
        )
        managed[config.connection_id] = task

    def cancel(connection_id):
        task = managed.pop(connection_id, None)
        if task and not task.done():
            task.cancel()
        sigs.pop(connection_id, None)

    while not stop_event.is_set():
        try:
            # reconcile expects sync spawn_fn/cancel_fn; wrap async spawn
            pending_spawns = []

            def sync_spawn(cfg):
                pending_spawns.append(cfg)
            reconcile(managed, sigs, sync_spawn, cancel)
            for cfg in pending_spawns:
                await spawn(cfg)
            # 清理已完成的 task
            done = [cid for cid, t in managed.items() if t.done()]
            for cid in done:
                task = managed.pop(cid)
                try:
                    task.result()  # raise if crashed
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"[conn {cid}] task exited with: {e}")
                # Note: do NOT restart here — next reconcile will if still active
        except Exception as e:
            logger.exception(f"Reconcile loop error: {e}")
        # Wait for next tick (or stop)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RECONCILE_SECONDS)
        except asyncio.TimeoutError:
            pass

    # Shutdown
    logger.info("Cancelling all client tasks...")
    for cid, task in list(managed.items()):
        task.cancel()
    if managed:
        await asyncio.gather(*managed.values(), return_exceptions=True)
    logger.info("MQTT listener stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: 验证 CLI 启动（不连真实 broker，仅看 reconcile 不崩）**

Run: `uv run python -m lightmes.connectivity.mqtt_listener &` 后 3 秒杀掉，看 log。

更简单的 smoke test：

```bash
uv run python -c "
import asyncio
from lightmes.modules.connectivity.mqtt_listener.__main__ import main
# 给 1 秒就退出
async def run():
    task = asyncio.create_task(main())
    await asyncio.sleep(1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
asyncio.run(run())
"
```

应看到 `MQTT listener starting` + 1 秒内 reconcile（不报错，因为 dev DB 无 active connection）。

- [ ] **Step 5: 写集成测试（默认 skip）**

创建 `tests/modules/connectivity/test_listener_integration.py`：

```python
"""Integration tests for MQTT listener — requires real broker.

Run with: pytest tests/modules/connectivity/test_listener_integration.py --run-broker-tests

Default skipped (no broker).
"""
import asyncio
import os

import pytest

BROKER_HOST = os.environ.get("TEST_MQTT_BROKER_HOST")
RUN_BROKER_TESTS = os.environ.get("RUN_BROKER_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_BROKER_TESTS or not BROKER_HOST,
    reason="需要 RUN_BROKER_TESTS=1 + TEST_MQTT_BROKER_HOST 环境变量",
)


@pytest.mark.asyncio
async def test_listener_picks_up_new_connection(db_session):
    """新建 active connection → listener 进程自动 spawn client task → 收到消息入库。"""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineMessage, MachineTopic, MqttConnection,
    )
    from lightmes.modules.connectivity.service import ConnectivityService
    from lightmes.database import SessionLocal

    # 1. 创建 active connection + topic
    svc = ConnectivityService(db_session)
    conn, _ = svc.create_connection(
        name="integration-1",
        broker_host=BROKER_HOST, broker_port=1883,
    )
    svc.activate_connection(conn.id)
    svc.add_topic(conn.id, "test/integration/+", "plain")
    db_session.commit()

    # 2. 启动 listener 进程
    import subprocess
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "lightmes.connectivity.mqtt_listener"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        # 3. 等 listener reconcile (5s) + connect
        await asyncio.sleep(8)

        # 4. publish 一条消息
        import aiomqtt
        async with aiomqtt.Client(hostname=BROKER_HOST, port=1883) as pub:
            await pub.publish("test/integration/hello", b"world", qos=0)

        # 5. 等消息入库（listener 收 + persist）
        await asyncio.sleep(2)

        # 6. 验证
        db = SessionLocal()
        try:
            msgs = list(db.execute(
                select(MachineMessage).where(
                    MachineMessage.machine_connection_id == conn.id
                )
            ).scalars().all())
            assert len(msgs) >= 1
            assert msgs[0].topic == "test/integration/hello"
            assert msgs[0].raw_payload == "world"
            assert msgs[0].processing_status == "ok"
        finally:
            db.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
```

注意：集成测试需要 `pytest-asyncio`。如未安装，加到 dev deps：

```bash
uv add --dev pytest-asyncio
```

并在 `pyproject.toml` `[tool.pytest.ini_options]` 加：

```toml
asyncio_mode = "auto"
```

- [ ] **Step 6: 运行测试（集成测试默认 skip）**

Run: `uv run pytest tests/modules/connectivity/ -v`
Expected: Task 1-5 单元测试 PASS，集成测试 skip。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/connectivity/mqtt_listener/ \
        tests/modules/connectivity/test_listener_integration.py \
        pyproject.toml
git commit -m "feat(connectivity): async MQTT client + CLI entry + supervisor main loop"
```

---

### Task 7: 回归 + memory 更新

**Files:**
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_lightmes.md`（追加 connectivity 模块）
- Test: 验证全套不破坏其他模块

- [ ] **Step 1: 全套 connectivity 测试**

Run: `uv run pytest tests/modules/connectivity/ -v`
Expected: 单元测试全 PASS（~30 tests），集成测试 skip。

- [ ] **Step 2: 全套项目回归**

Run: `uv run pytest tests/modules/ -v`
Expected: 不引入新失败（pre-existing 已知失败 OUT OF SCOPE）。

- [ ] **Step 3: 验证 alembic 迁移可重复应用**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: 无报错。

- [ ] **Step 4: 更新 memory**

修改 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_lightmes.md`，在末尾追加：

```markdown

## Connectivity (MQTT 数采核心栈) — 2026-08-13

- 4 张新表：`machine_connections`（协议无关连接）、`mqtt_connections`（MQTT broker 配置，密码 Fernet 加密）、`machine_topics`（订阅 + 通配符）、`machine_messages`（append-only 日志）
- 独立监听进程 `python -m lightmes.connectivity.mqtt_listener`：asyncio supervisor 每 5s reconcile DB，单进程多连接，指数退避重连（cap 5min）
- Admin UI: `/connectivity/connections`（列表 + 创建 + 启停 + 删除）+ `/connectivity/connections/{id}`（详情 + Topic CRUD + 最近 100 条消息）
- Topic 通配符匹配：MQTT 标准 `+` 单层 / `#` 多层后缀
- 协议当前只接受 mqtt（V3+ 加 opcua/modbus）
- V1 不含解析引擎和 Action 系统（数采-2 实现）
- 密码加密用 `cryptography.fernet.Fernet` + PBKDF2 派生 key（复用 settings.secret_key）
- 监听器消息入库独立 SessionLocal，不污染 request session
- 集成测试默认 skip（需真实 broker，通过 `RUN_BROKER_TESTS=1` + `TEST_MQTT_BROKER_HOST` 启用）
```

- [ ] **Step 5: Commit (不含 memory 文件)**

memory 文件在仓库外，不进 git。此 task 不需要新 commit（如果上面 task 已经全部 commit）。

---

## 任务依赖

```
Task 1 (models + crypto + topic_match + migration)
  ↓
Task 2 (CRUD service + repository)
  ↓
Task 3 (admin UI: connection list + create)
  ↓
Task 4 (admin UI: detail + Topic CRUD + messages view)
  ↓
Task 5 (message service + supervisor reconcile)
  ↓
Task 6 (async MQTT client + CLI entry + supervisor main loop)
  ↓
Task 7 (回归 + memory)
```

顺序执行。

## 全套回归（任意 task 完成后均可运行）

```bash
uv run pytest tests/modules/connectivity/ -v
uv run alembic upgrade head
```

## 手工最终验收（Task 7 完成后）

```bash
# 终端 1: 启动 mosquitto broker（或用工厂已有 broker）
docker run --rm -p 1883:1883 eclipse-mosquitto mosquitto -c /mosquitto-no-auth.conf

# 终端 2: 启动 LightMES API
uv run uvicorn lightmes.main:app --port 8000

# 终端 3: 启动 MQTT 监听器
uv run python -m lightmes.connectivity.mqtt_listener

# 浏览器：登录 admin → 数采 → 创建连接 (name=test, broker_host=localhost, port=1883) → 启用 → 进详情 → 加 topic (test/hello, plain)

# 终端 4: 发测试消息
mosquitto_pub -h localhost -t test/hello -m "world"

# 浏览器刷新详情页 → 应看到消息
```
