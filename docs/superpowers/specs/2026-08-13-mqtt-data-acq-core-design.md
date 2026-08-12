# 数采-1：MQTT 核心栈 - 设计文档

**日期**: 2026-08-13
**状态**: Approved
**关联**: 借鉴 OpenMES Connectivity 模块（`C:\Users\zhaocao\Documents\GitHub\OpenMes\backend\app\Services\Connectivity\`）

---

## 1. 背景与目标

### 1.1 现状

LightMES 当前**完全无数采能力**：
- `config.py:10` 仅有一个 `mqtt_url` 配置项未被任何代码使用
- `integration/` 模块只做 ERP CSV/JSON 主数据导入
- 无 MQTT/OPC-UA/Modbus 客户端、无 topic 订阅、无消息日志

笔记本壳装配产线的 PLC、设备、检测仪器若要发数据给 MES，目前完全做不到。这意味着：
- 设备产量不能自动计入 WO produced_qty
- 设备报警不能自动触发 Andon
- OEE 计算缺数据源（手工录入不可持续）

### 1.2 目标（数采-1 范围）

构建 **MQTT 协议的核心数采栈**（5 spec 中的第 1 个）：
- **CRUD 管理 MQTT 连接 + Topic 订阅**（DB 驱动热配置）
- **独立监听进程**接收消息 → 入库 `machine_messages`
- **Admin UI** 查看 / 操作连接状态 + 浏览消息
- **Topic 通配符匹配**（MQTT `+` / `#`）

### 1.3 非目标（明确不做）

- ❌ **解析引擎**（JSON / JSONPath / csv / hex）—— 数采-2
- ❌ **Action 系统**（7 种 action：update_work_order_qty / create_issue 等）—— 数采-2
- ❌ **TopicMapping 表**（无 action 时 mapping 无意义）—— 数采-2
- ❌ **OPC-UA / Modbus 协议** —— 数采-3 / 数采-4
- ❌ Webhook 转发 —— 数采-5
- ❌ 消息保留策略 cron —— 数采-5
- ❌ Andon 自动触发（依赖 Andon 模块先做）
- ❌ 实时消息 WebSocket 推送（admin UI 用 HTMX polling）
- ❌ 消息批量导出（CSV）

---

## 2. 总体架构

```
       PLC / 设备 / Edge Gateway
              │ MQTT publish
              ▼
       ┌─────────────────┐
       │  MQTT Broker    │  (mosquitto / EMQX / 工厂现有)
       └────────┬────────┘
                │
                │ subscribe
                ▼
┌───────────────────────────────┐    ┌──────────────────────────┐
│  Listener Process (separate)  │    │  FastAPI Process         │
│  python -m lightmes.          │    │  (HTTP /api/v1 + /mcp)   │
│  connectivity.mqtt_listener   │    │                          │
│                               │    │  Service 层共享          │
│  - asyncio supervisor         │    │  ConnectivityService     │
│  - 每连接 1 个 task            │    │                          │
│  - 5s reconcile DB            │    │  Admin UI:               │
│  - 自动重连 backoff           │    │  /connectivity/* (CRUD)  │
│                               │    │                          │
│  收消息 → MachineMessage 入库  │    │                          │
└─────────────┬─────────────────┘    └──────────────┬───────────┘
              │                                     │
              └─────────────┬───────────────────────┘
                            ▼
                  PostgreSQL (共享)
                  machine_connections
                  mqtt_connections
                  machine_topics
                  machine_messages
```

### 2.1 关键决策

1. **独立监听进程** —— 不用 FastAPI lifespan 后台任务，单独 `python -m lightmes.connectivity.mqtt_listener`：
   - 与 API 进程解耦（API 重启不丢消息）
   - 可独立扩缩容（多机部署时一个监听器接多 broker）
   - 由 docker-compose / systemd 监督（matches OpenMES 模式）

2. **MQTT 客户端库** —— `aiomqtt`（基于 paho-mqtt 的 asyncio 封装）：
   - 纯 async，与 FastAPI 同款
   - 单进程可同时管理多个 broker 连接
   - 成熟，活跃维护

3. **DB 驱动热配置** —— 监听器不读配置文件，每 5 秒查 DB：
   - 新增 connection → 自动连接订阅
   - 停用 connection → 自动断开
   - Topic 变更 → 重新订阅
   - 无需重启

4. **共享 service 层** —— `ConnectivityService` 同时给 FastAPI（admin CRUD）和 listener 使用。

### 2.2 启动方式

```bash
# 启动 API（已有）
uv run uvicorn lightmes.main:app --port 8000

# 启动 MQTT 监听器（新）
uv run python -m lightmes.connectivity.mqtt_listener

# 或 docker-compose 部署：API 容器 + Listener 容器 + MQTT broker 容器
```

---

## 3. 数据模型

### 3.1 `machine_connections` — 协议无关连接抽象

```python
class MachineConnection(Base, TimestampMixin):
    __tablename__ = "machine_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    protocol: Mapped[str] = mapped_column(String(20), default="mqtt")
        # V1 只有 mqtt；V3+ 加 opcua/modbus
    is_active: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
        # disconnected / connecting / connected / error
    status_message: Mapped[str | None] = mapped_column(String(500), default=None)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    messages_received: Mapped[int] = mapped_column(default=0)
```

**业务约束**：
- `name` 全局唯一
- `protocol` 当前只接受 `"mqtt"`
- 状态机：`disconnected` → `connecting` → `connected` / `error` → `disconnected`
- `status_message`：连接错误时存原因（broker 拒绝、TLS 失败等），截断 500 字

### 3.2 `mqtt_connections` — MQTT broker 配置（一对一）

```python
class MqttConnection(Base, TimestampMixin):
    __tablename__ = "mqtt_connections"
    __table_args__ = (
        UniqueConstraint("machine_connection_id", name="uq_mqtt_per_machine_connection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    broker_host: Mapped[str] = mapped_column(String(255))
    broker_port: Mapped[int] = mapped_column(default=1883)
    client_id: Mapped[str | None] = mapped_column(String(100), default=None)
        # 不设则自动生成 lightmes-{connection_id}-{8 hex}
    username: Mapped[str | None] = mapped_column(String(100), default=None)
    password_encrypted: Mapped[str | None] = mapped_column(String(500), default=None)
        # Fernet 对称加密（复用 settings.secret_key）
    use_tls: Mapped[bool] = mapped_column(default=False)
    keep_alive_seconds: Mapped[int] = mapped_column(default=60)
    qos_default: Mapped[int] = mapped_column(default=0)  # 0 / 1 / 2
    clean_session: Mapped[bool] = mapped_column(default=True)
    connect_timeout_seconds: Mapped[int] = mapped_column(default=10)
    reconnect_delay_seconds: Mapped[int] = mapped_column(default=5)
```

**业务约束**：
- `machine_connection_id` 唯一（一对一）
- `qos_default` ∈ {0, 1, 2}（Pydantic 校验）
- `broker_port` 1-65535
- 删除 machine_connection 级联删除 mqtt_connection（ondelete CASCADE）
- **密码加密**：用 `cryptography.fernet.Fernet` 对称加密，明文永不进 DB

### 3.3 `machine_topics` — 订阅主题

```python
class MachineTopic(Base, TimestampMixin):
    __tablename__ = "machine_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    topic_pattern: Mapped[str] = mapped_column(String(500))
        # 支持 MQTT 通配符：+ 单层、# 多层
        # 例如：machine/line1/+/count 或 machine/line1/#
    payload_format: Mapped[str] = mapped_column(String(20), default="json")
        # V1 仅记录元数据，不解析（数采-2 实现 MqttMessageParser）
        # 接受值：json / plain / csv / hex（V1 全部原样存 raw_payload）
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
```

**业务约束**：
- 同 `(machine_connection_id, topic_pattern, is_active=True)` 唯一（避免重复订阅）
- 监听器匹配时支持通配符（`+` 单层、`#` 多层）

### 3.4 `machine_messages` — 原始消息日志（append-only）

```python
class MachineMessage(Base):
    """Append-only — 无 updated_at。"""
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
        # 哪条 MachineTopic 配置匹配到此消息（NULL = 未匹配）
    processing_status: Mapped[str] = mapped_column(String(20), default="ok")
        # V1 只用 ok（已入库）/ skipped（未匹配 topic）
        # 数采-2 加 error（解析/action 失败）
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
```

**业务约束**：
- Append-only（无 `updated_at`）
- `processing_status` V1 只产出 `ok` / `skipped`
- 删除 MachineConnection 级联删除其 messages（ondelete CASCADE）
- 删除 MachineTopic 时 `matched_topic_id` SET NULL（保留消息历史，失去 topic 关联）

### 3.5 Alembic 迁移

一次迁移建 4 张表 + 索引 + 约束。`down_revision` = master HEAD（最新）。

---

## 4. 监听器内部实现

### 4.1 进程结构

```
python -m lightmes.connectivity.mqtt_listener
        │
        ├── SIGTERM/SIGINT handler（优雅退出）
        │
        ├── async supervisor task（主循环）
        │     │
        │     ├── 每 5 秒 reconcile DB
        │     │     - 新增 active connection → spawn client task
        │     │     - 停用 / 删除 connection → cancel client task
        │     │     - Topic 变更 → reconnect client（重新订阅）
        │     │
        │     └── 监控 task 状态，失败 → 标记 error → 待 reconcile 重试
        │
        ├── 每个 connection 1 个 client task（asyncio）
        │     │
        │     ├── connect() → 标记 connected
        │     ├── subscribe(active_topics)
        │     ├── loop: async for message in client.messages
        │     │     │
        │     │     ├── 匹配 MachineTopic（通配符）
        │     │     ├── 写 MachineMessage（独立 SessionLocal，避免污染）
        │     │     ├── 递增 machine_connections.messages_received
        │     │     └── 异常 → log + 状态标 error
        │     │
        │     ├── 断开 → 标记 disconnected → 退出 task
        │     └── 协议级异常 → 标记 error → 退出 task
        │
        └── 优雅退出：cancel 所有 client task → 等待清理
```

### 4.2 连接配置签名

`mqtt_connections` 的密码字段加密存储。监听器读取配置时解密：

```python
class MqttClientConfig:
    """Resolved config for one MQTT client (decrypted, validated)."""
    connection_id: int
    broker_host: str
    broker_port: int
    client_id: str  # 自动生成 if None
    username: str | None
    password: str | None  # 解密后
    use_tls: bool
    keep_alive_seconds: int
    qos_default: int
    clean_session: bool
    connect_timeout_seconds: int
    reconnect_delay_seconds: int
    topics: list[MachineTopic]  # active topics
```

### 4.3 Topic 通配符匹配

复刻 OpenMES `MachineTopic.matchesTopic()`：

```python
def matches_topic(pattern: str, topic: str) -> bool:
    """MQTT wildcard matching. + = one level, # = multi level suffix."""
    if pattern == topic:
        return True
    # # multi-level
    if pattern.endswith("#"):
        prefix = pattern[:-1].rstrip("/")
        return prefix == "" or topic.startswith(prefix + "/") or topic == prefix
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

### 4.4 消息处理流程

```python
async def _on_message(connection_id: int, topic: str, payload: bytes, received_at: datetime):
    """收消息 → 匹配 topic → 入库。同步阻塞、独立 session。"""
    db = SessionLocal()
    try:
        # 1. 查所有 active topics for this connection
        topics = db.execute(
            select(MachineTopic).where(
                MachineTopic.machine_connection_id == connection_id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all()
        # 2. 找第一个匹配的（同 OpenMES 行为）
        matched = next((t for t in topics if matches_topic(t.topic_pattern, topic)), None)
        # 3. 入库
        msg = MachineMessage(
            machine_connection_id=connection_id,
            topic=topic,
            raw_payload=payload.decode("utf-8", errors="replace"),
            matched_topic_id=matched.id if matched else None,
            processing_status="ok" if matched else "skipped",
            received_at=received_at,
        )
        db.add(msg)
        # 4. 递增连接消息计数
        db.execute(
            update(MachineConnection)
            .where(MachineConnection.id == connection_id)
            .values(messages_received=MachineConnection.messages_received + 1)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Message persist failed: conn={connection_id} topic={topic} err={e}")
    finally:
        db.close()
```

### 4.5 重连策略

```python
async def run_client_with_reconnect(config: MqttClientConfig, stop_event: asyncio.Event):
    """单连接循环：连接 → 订阅 → 收消息 → 失败 → 退避 → 重连。"""
    backoff = config.reconnect_delay_seconds
    max_backoff = 300  # 5 min cap
    while not stop_event.is_set():
        try:
            _mark_status(config.connection_id, "connecting", db)
            async with Client(config.broker_host, port=config.broker_port, ...) as client:
                for topic in config.topics:
                    await client.subscribe(topic.topic_pattern, qos=config.qos_default)
                _mark_status(config.connection_id, "connected", db, last_connected_at=now())
                async for message in client.messages:
                    await _on_message(config.connection_id,
                                      str(message.topic),
                                      message.payload,
                                      datetime.now(timezone.utc))
                _mark_status(config.connection_id, "disconnected", db)
                break
        except asyncio.CancelledError:
            break
        except MqttError as e:
            _mark_status(config.connection_id, "error", db, message=str(e))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
        except Exception as e:
            _mark_status(config.connection_id, "error", db, message=str(e))
            logger.exception("MQTT client crashed")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
```

### 4.6 Supervisor reconcile 逻辑

```python
async def reconcile(managed: dict[int, asyncio.Task], sigs: dict[int, str]):
    """5 秒一次：对比 DB active connections 与内存中 managed tasks。"""
    db = SessionLocal()
    try:
        active = db.execute(
            select(MachineConnection).where(
                MachineConnection.is_active.is_(True),
                MachineConnection.protocol == "mqtt",
            )
        ).scalars().all()
        active_ids = {c.id for c in active}
        managed_ids = set(managed.keys())
        # 新增 → spawn
        for c in active:
            if c.id not in managed_ids:
                config = _resolve_mqtt_config(db, c)
                sig = _compute_sig(config)
                task = asyncio.create_task(run_client_with_reconnect(config, ...))
                managed[c.id] = task
                sigs[c.id] = sig
        # 删除 → cancel
        for cid in managed_ids - active_ids:
            managed[cid].cancel()
            del managed[cid]
            sigs.pop(cid, None)
        # 配置变更 → cancel（reconcile 下轮重启）
        for c in active:
            new_sig = _compute_sig(_resolve_mqtt_config(db, c))
            if sigs.get(c.id) != new_sig:
                managed[c.id].cancel()
    finally:
        db.close()
```

### 4.7 模块结构

```
src/lightmes/modules/connectivity/
├── __init__.py              # register(app) — admin UI 路由
├── models.py                # 4 张表的 SQLAlchemy 模型
├── schemas.py               # Pydantic schemas（CRUD 用）
├── repository.py            # CRUD 操作
├── service.py               # 业务逻辑（创建/更新连接 + topic）
├── router.py                # admin UI HTML 路由
├── crypto.py                # Fernet 密码加密/解密
├── topic_match.py           # 通配符匹配函数（独立可测）
└── mqtt_listener/
    ├── __init__.py
    ├── __main__.py           # CLI entry: asyncio.run(main())
    ├── supervisor.py         # 主循环 + reconcile
    └── client.py             # 单连接 client task（含重连）
```

---

## 5. Admin UI（HTMX 标准风格，不走 planner.css）

### 5.1 路由清单

```
GET    /connectivity                                   # 主页（重定向到 /connections）
GET    /connectivity/connections                       # 连接列表 + 新建表单
POST   /connectivity/connections                       # 创建（含 mqtt_connection 嵌套字段）
GET    /connectivity/connections/{id}                  # 详情 + Topic + 最近消息
POST   /connectivity/connections/{id}/activate         # 启用（is_active=True）
POST   /connectivity/connections/{id}/deactivate       # 停用
POST   /connectivity/connections/{id}/delete           # 删除（级联）
POST   /connectivity/connections/{conn_id}/topics      # 添加 topic
POST   /connectivity/connections/{conn_id}/topics/{tid}/toggle  # 切换 active
POST   /connectivity/connections/{conn_id}/topics/{tid}/delete  # 删除 topic
```

全部 admin only（`Depends(require_role("admin", "supervisor"))`，复用现有依赖）。

### 5.2 列表页 + 创建表单

`/connectivity/connections`：表格列出所有连接（id / name / broker / status / messages_received / last_connected_at）+ 创建表单（name / broker_host / broker_port / username / password / use_tls）。

### 5.3 详情页

`/connectivity/connections/{id}`：
- 连接基本信息 + 启用/停用按钮
- Topic 列表 + 添加表单 + toggle/delete 按钮
- 最近 100 条消息（topic / payload 截断 200 字 / 匹配状态）

---

## 6. 测试策略

### 6.1 单元测试

| 测试 | 覆盖点 |
|---|---|
| `test_topic_match_exact` | 精确匹配 |
| `test_topic_match_plus_wildcard` | `+` 单层通配符 |
| `test_topic_match_hash_wildcard` | `#` 多层通配符 |
| `test_topic_match_no_match` | 不匹配场景 |
| `test_crypto_encrypt_decrypt_round_trip` | Fernet 加密/解密一致 |
| `test_crypto_decrypt_wrong_key_returns_none` | 错误 key 不抛异常，返回 None |
| `test_connection_create_validates_protocol` | 非 mqtt 协议 → ValidationError |
| `test_connection_create_encrypts_password` | DB 不存明文 |
| `test_connection_create_unique_name` | 重名 → BusinessRuleError |
| `test_topic_create_validates_format` | payload_format 必须在 4 个值中 |
| `test_topic_create_unique_active_per_connection` | 同连接同 pattern active 唯一 |
| `test_message_persist_increments_count` | 消息入库 + 计数器递增 |
| `test_message_persist_skipped_when_no_topic_match` | 无匹配 → processing_status=skipped |
| `test_page_connections_list_requires_admin` | 非 admin → 403 |
| `test_page_connections_create_post` | POST 创建成功 |
| `test_page_topic_toggle` | 切换 active 状态 |

### 6.2 集成测试（可选，默认 skip）

需要真实 MQTT broker（mosquitto 容器）：

```python
# 用 mosquitto 容器 + aiomqtt publish + 等消息入库
@pytest.fixture
def mqtt_broker():
    # docker run -p 1883:1883 eclipse-mosquitto
    ...

def test_listener_receives_and_persists(mqtt_broker):
    # 1. 创建 active connection + topic
    # 2. 启动 listener 进程（subprocess）
    # 3. publish 一条消息
    # 4. 等 2 秒
    # 5. 查 DB 验证 MachineMessage 入库
```

加 `--run-broker-tests` 启用。

---

## 7. 新增依赖

```toml
[project]
dependencies = [
    # ... existing ...
    "aiomqtt>=2.3.0",          # MQTT async client
    "cryptography>=45.0.0",    # Fernet for password encryption
]
```

---

## 8. 任务拆分（预估 8 task）

1. **Migration + Models + crypto + topic_match** — 4 张表 + Fernet + 通配符匹配函数（含单元测试）
2. **Repository + Service** — CRUD + 业务约束校验
3. **Admin UI 列表 + 创建表单** — `/connectivity/connections` GET/POST
4. **Admin UI 详情 + Topic CRUD** — `/connectivity/connections/{id}` + topic toggle/delete
5. **Admin UI 消息查看** — 最近 100 条消息分页
6. **Listener supervisor + reconcile** — 主循环 + 5s reconcile DB
7. **Listener client task + 重连** — aiomqtt 集成 + 消息入库 + 指数退避
8. **Listener CLI entry + 集成测试 + memory**

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| aiomqtt 版本兼容性 | 锁 `aiomqtt>=2.3,<3.0`；2.x stable |
| 监听器进程挂了无监督 | docker-compose `restart: always` 或 systemd unit |
| 单进程多 broker 连接数上限 | asyncio 单进程可稳定管理 50+ 连接；超量时分进程 |
| Broker 网络抖动导致 backoff 雪崩 | 指数退避 cap 5 分钟；每连接独立 backoff |
| 消息洪峰打爆 DB | 写入是 INSERT（廉价）；消息表分区/清理留 V5 |
| Topic 通配符误配导致订阅过宽（`#`） | 接受（用户责任）；admin UI 列表展示当前 topics 可视化检查 |
| 密码泄露 | Fernet 加密 + DB 查询永不返回 password_encrypted |
