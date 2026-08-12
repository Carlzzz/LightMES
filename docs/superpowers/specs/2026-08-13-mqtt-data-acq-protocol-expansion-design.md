# 数采-3+4：OPC-UA + Modbus 协议扩展 - 设计文档

**日期**: 2026-08-13
**关联**: 数采-1（MQTT 核心栈）+ 数采-2（解析+Action）

---

## 1. 范围

在现有 MQTT 数采架构上扩展两种工业协议：
- **OPC-UA**（`asyncua` 库）：订阅/轮询 OPC-UA server 的 node 值
- **Modbus TCP**（`pymodbus` 库）：轮询保持寄存器/线圈

复用已有 `MachineConnection`（protocol 字段）+ `MachineMessage` + `TopicMapping` + `ActionExecutor`。新增协议特定的连接配置表 + 客户端模块。

---

## 2. 数据模型

### 2.1 `opcua_connections`（新增）

```python
class OpcuaConnection(Base, TimestampMixin):
    __tablename__ = "opcua_connections"
    __table_args__ = (UniqueConstraint("machine_connection_id", name="uq_opcua_per_mc"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    server_url: Mapped[str] = mapped_column(String(500))  # opc.tcp://192.168.1.10:4840
    security_mode: Mapped[str] = mapped_column(String(20), default="none")  # none/sign/sign_encrypt
    username: Mapped[str | None] = mapped_column(String(100), default=None)
    password_encrypted: Mapped[str | None] = mapped_column(String(500), default=None)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=5)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
```

### 2.2 `modbus_connections`（新增）

```python
class ModbusConnection(Base, TimestampMixin):
    __tablename__ = "modbus_connections"
    __table_args__ = (UniqueConstraint("machine_connection_id", name="uq_modbus_per_mc"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_connection_id: Mapped[int] = mapped_column(ForeignKey("machine_connections.id", ondelete="CASCADE"), index=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=502)
    slave_id: Mapped[int] = mapped_column(Integer, default=1)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=5)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
```

### 2.3 MachineTopic 扩展

`topic_pattern` 字段语义随协议变化：
- **MQTT**：topic 订阅模式（`machine/+/count`）
- **OPC-UA**：node ID（`ns=2;s=Temperature`）
- **Modbus**：register spec（`holding_register:0:10` = 读保持寄存器地址0长度10）

`payload_format` 语义：
- **MQTT**：原始消息格式（json/plain/csv/hex）
- **OPC-UA**：总是 `json`（`{"value": <number>, "source_timestamp": "..."}`）
- **Modbus**：总是 `json`（`{"address": 0, "values": [1, 2, 3]}`）

无需新字段，复用现有 `topic_pattern` + `payload_format`。

---

## 3. 客户端模块

### 3.1 OPC-UA Client (`opcua_client.py`)

```python
async def run_opcua_client(config: ResolvedOpcuaConfig, stop_event: asyncio.Event):
    """连接 OPC-UA server → 轮询 nodes → persist_message。"""
    from asyncua import Client
    while not stop_event.is_set():
        try:
            mark_status(config.connection_id, "connecting")
            async with Client(config.server_url, timeout=config.connect_timeout) as client:
                mark_status(config.connection_id, "connected")
                while not stop_event.is_set():
                    for node_spec in config.topics:
                        node = client.get_node(node_spec.topic_pattern)
                        value = await node.read_value()
                        payload = json.dumps({"value": str(value), "node": node_spec.topic_pattern})
                        persist_message(config.connection_id, node_spec.topic_pattern,
                                       payload.encode(), datetime.now(timezone.utc))
                    await asyncio.sleep(config.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            mark_status(config.connection_id, "error", message=str(e))
            await asyncio.sleep(config.reconnect_delay)
```

### 3.2 Modbus Client (`modbus_client.py`)

```python
async def run_modbus_client(config: ResolvedModbusConfig, stop_event: asyncio.Event):
    """连接 Modbus TCP → 轮询 registers → persist_message。"""
    from pymodbus.client import AsyncModbusTcpClient
    while not stop_event.is_set():
        try:
            mark_status(config.connection_id, "connecting")
            client = AsyncModbusTcpClient(config.host, port=config.port, timeout=config.connect_timeout)
            await client.connect()
            if not client.connected:
                raise ConnectionError(f"Modbus connect failed: {config.host}:{config.port}")
            mark_status(config.connection_id, "connected")
            while not stop_event.is_set():
                for reg_spec in config.topics:
                    # Parse: "holding_register:0:10" → read 10 holding registers starting at 0
                    parts = reg_spec.topic_pattern.split(":")
                    reg_type = parts[0]
                    addr = int(parts[1])
                    count = int(parts[2]) if len(parts) > 2 else 1
                    result = await client.read_holding_registers(addr, count, slave=config.slave_id)
                    values = result.registers if not result.isError() else []
                    payload = json.dumps({"address": addr, "values": values})
                    persist_message(config.connection_id, reg_spec.topic_pattern,
                                   payload.encode(), datetime.now(timezone.utc))
                await asyncio.sleep(config.poll_interval_seconds)
            client.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            mark_status(config.connection_id, "error", message=str(e))
            await asyncio.sleep(config.reconnect_delay)
```

### 3.3 Supervisor 扩展

`supervisor.py` 的 `reconcile()` 函数扩展：
- 查 active connections with `protocol == "opcua"` → spawn `run_opcua_client`
- 查 active connections with `protocol == "modbus"` → spawn `run_modbus_client`
- MQTT connections → existing `run_client_with_reconnect` (unchanged)

### 3.4 `resolve_config` 扩展

`resolve_config` 函数根据 `conn.protocol` 查对应的协议配置表：
- `"mqtt"` → 查 `mqtt_connections`（已有）
- `"opcua"` → 查 `opcua_connections`（新增）
- `"modbus"` → 查 `modbus_connections`（新增）

返回 `ResolvedConnectionConfig`（已有 dataclass，扩展字段）。

---

## 4. Admin UI

Connection create form 增加 protocol 选择：
- MQTT（现有表单）
- OPC-UA（server_url / security_mode / username / password / poll_interval）
- Modbus（host / port / slave_id / poll_interval）

创建后根据 protocol 显示不同详情字段。

---

## 5. 新增依赖

```toml
"asyncua>=1.9.0",
"pymodbus>=3.7.0",
```

---

## 6. 任务拆分（3 task，合并执行）

1. **Migration + Models + resolve_config 扩展** — opcua_connections + modbus_connections 表 + 模型 + resolve_config 多协议
2. **OPC-UA Client + Modbus Client + Supervisor 集成** — opcua_client.py + modbus_client.py + reconcile 扩展
3. **Admin UI protocol 选择 + 回归 + memory**

---

## 7. 非目标

- OPC-UA subscription 模式（先用轮询，V6 再加 subscription）
- Modbus RTU（串口，仅 TCP）
- OPC-UA Security certificate 管理（V1 用 none 模式）
- Modbus 写寄存器（V1 仅读）
