# 数采-2：解析引擎 + Action 系统 - 设计文档

**日期**: 2026-08-13
**状态**: Approved
**关联**: 数采-1（MQTT 核心栈）的升级；借鉴 OpenMES `MqttMessageParser` + `ActionExecutor` + `TopicMapping`

---

## 1. 背景与目标

### 1.1 现状

数采-1 完成了 MQTT 核心栈：监听进程接收消息 → 匹配 topic → 入库 `machine_messages`（`processing_status: ok/skipped`）。但消息只是"存起来看"，不能**驱动业务**。

### 1.2 目标

为收到的 MQTT 消息添加**解析 + 行动**能力：
- **解析引擎**：JSON / plain / csv / hex 格式 + JSONPath 字段提取 + 条件表达式
- **TopicMapping**：每条 topic 可配置多条 mapping（规则），按优先级执行
- **6 个 Action**：适配 LightMES 已有模型的业务动作
- **增强 persist_message**：消息处理从"仅入库"升级为"解析 → 条件过滤 → 执行 actions → 入库"

### 1.3 非目标

- ❌ OPC-UA / Modbus 协议 —— 数采-3 / 数采-4
- ❌ 消息清理 cron / 实时 WebSocket —— 数采-5
- ❌ Andon 自动触发（create_issue）—— 依赖 Andon 模块
- ❌ LineStatus 驱动（update_line_status）—— 无 LineStatus 模型
- ❌ Batch step 推进（update_batch_step）—— LightMES 无 batch 概念

---

## 2. 数据模型

### 2.1 新增表：`topic_mappings`

```python
class TopicMapping(Base, TimestampMixin):
    __tablename__ = "topic_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_topic_id: Mapped[int] = mapped_column(
        ForeignKey("machine_topics.id", ondelete="CASCADE"), index=True)
    description: Mapped[str | None] = mapped_column(String(255), default=None)
    field_path: Mapped[str | None] = mapped_column(String(255), default=None)
    action_type: Mapped[str] = mapped_column(String(30))
    action_params: Mapped[dict | None] = mapped_column(JSON, default=None)
    condition_expr: Mapped[str | None] = mapped_column(String(255), default=None)
    priority: Mapped[int] = mapped_column(default=100)
    is_active: Mapped[bool] = mapped_column(default=True)
```

**业务约束**：
- `action_type` 只接受 6 个值（DB CheckConstraint）
- 删除 MachineTopic 级联删除 mappings（CASCADE）

### 2.2 MachineMessage 新增字段（ALTER TABLE）

```python
# 在已有字段基础上追加：
parsed_data: Mapped[dict | None] = mapped_column(JSON, default=None)
actions_triggered: Mapped[list | None] = mapped_column(JSON, default=None)
processing_error: Mapped[str | None] = mapped_column(Text, default=None)
```

---

## 3. 解析引擎（MqttMessageParser）

### 3.1 格式解析

```python
def parse(payload: str, format: str) -> dict:
    """按声明格式解析原始 payload。失败返回 {'_raw': payload, '_error': '...'}。"""
    # json → json.loads
    # plain → {"value": payload}
    # csv → {"rows": [[...], ...]}
    # hex → {"hex": payload, "bytes": [...]}
```

### 3.2 JSONPath 字段提取

```python
def resolve_path(path: str | None, data: dict) -> any:
    """
    "$.field" → data["field"]
    "$.nested.field" → data["nested"]["field"]
    "$.arr.0" → data["arr"][0]
    无 "$" 前缀 → 字面值返回
    None → 返回整个 data
    """
```

### 3.3 条件表达式

```python
def evaluate_condition(expr: str | None, resolved_value) -> bool:
    """
    "value > 5" → resolved_value > 5
    "status == active" → resolved_value == "active"
    "code contains ERR" → "ERR" in str(resolved_value)
    None → True（总执行）
    不支持的表达式 → True（容错）
    """
```

---

## 4. Action 系统

### 4.1 6 个 Action

| Action | 说明 | action_params |
|---|---|---|
| `log_event` | 仅记录事件 | `{}` |
| `update_work_order_produced_qty` | 自动计数（增/绝对） | `{"work_order_code_path": "$.order_no", "qty_increment": true}` |
| `set_work_order_status` | 改工单状态 | `{"work_order_code_path": "$.order_no", "status": "in_progress"}` |
| `update_serial_unit_status` | 改 SN 状态 | `{"sn_path": "$.sn", "status": "scrapped"}` |
| `create_defect` | 自动缺陷登记 | `{"sn_path": "$.sn", "defect_type_code": "AUTO_SCRATCH"}` |
| `webhook_forward` | 转发外部系统 | `{"url": "https://erp.example.com/hook", "method": "POST"}` |

### 4.2 参数解析模式

所有 action 参数支持两种来源：
- `{key}_path: "$.field"` → 从 parsed_data 动态解析
- `{key}: "literal"` → 静态值

```python
def resolve_param(params: dict, key: str, data: dict, parser) -> any:
    path = params.get(f"{key}_path")
    if path:
        return parser.resolve_path(path, data)
    return params.get(key)
```

### 4.3 ActionExecutor

```python
class ActionExecutor:
    def execute_all(self, mappings, parsed_data) -> list[dict]:
        """遍历 mappings（按 priority），逐个执行，返回结果列表。"""

    def execute_single(self, mapping, parsed_data) -> dict:
        """
        1. resolve field_path → field_value
        2. evaluate condition_expr(field_value) → False? skip
        3. dispatch action_type(action_params, parsed_data, field_value)
        4. 返回 {mapping_id, action_type, status, message}
        """
```

### 4.4 Action 失败处理

- 每个 action 独立 try/except
- 失败记 `status="error"`, `message=exception_message`
- **不回滚已执行的 action 写操作**（部分成功 > 全回滚）
- persist_message 最终汇总：至少一个 ok → "ok"；全 error → "error"；全 skipped → "skipped"

---

## 5. 处理流程（persist_message 增强）

```
收到 MQTT 消息
    ↓
1. 匹配 MachineTopic（通配符，不变）
    ↓ 无匹配 → processing_status="skipped" → 入库 → 结束
    ↓ 有匹配
2. 解析 payload（新增）
    parsed_data = MqttMessageParser.parse(payload, matched.payload_format)
    ↓
3. 查该 topic 的 active TopicMappings（新增）
    ↓ 无 mappings → status="ok"（仅记录，无 actions）
    ↓ 有 mappings
4. ActionExecutor.execute_all(mappings, parsed_data)
    actions_triggered = [{mapping_id, action_type, status, message}, ...]
    ↓
5. 汇总 + 入库 MachineMessage
    - parsed_data: JSON
    - actions_triggered: JSON
    - processing_status: ok / error / skipped
    - processing_error: 错误摘要（截断 500 字）
```

**Action 各自内部 commit**（如 `_update_wo_qty` 增 produced_qty 后 commit）。
persist_message 的 commit 只负责写 MachineMessage 行。

---

## 6. Admin UI

### 6.1 新增路由

```
POST   /connectivity/connections/{conn_id}/topics/{topic_id}/mappings
POST   /connectivity/connections/{conn_id}/topics/{topic_id}/mappings/{mid}/toggle
POST   /connectivity/connections/{conn_id}/topics/{topic_id}/mappings/{mid}/delete
```

### 6.2 详情页扩展

每个 topic 行下方展开 TopicMapping 管理：
- Mapping 列表（优先级 / action_type / field_path / condition_expr / 状态 / 操作）
- 添加 mapping 表单（action_type 下拉 + field_path + condition_expr + action_params JSON 文本框）

### 6.3 消息详情增强

消息表格追加 Parsed + Actions 列：
- Parsed：parsed_data JSON 预览（折叠展开）
- Actions：actions_triggered 的 status 徽章列表

---

## 7. 测试策略

| 测试 | 覆盖点 |
|---|---|
| `test_parse_json` | JSON 格式解析 |
| `test_parse_plain` | plain 格式 |
| `test_parse_csv` | CSV 格式 |
| `test_parse_hex` | hex 格式 |
| `test_parse_invalid_json` | 无效 JSON → `_raw + _error` |
| `test_resolve_path_nested` | `$.a.b.c` 嵌套 |
| `test_resolve_path_array` | `$.arr.0` 数组 |
| `test_resolve_path_literal` | 无 `$` → 字面值 |
| `test_resolve_path_none` | None → 整个 data |
| `test_condition_gt` | `value > 5` |
| `test_condition_eq` | `status == active` |
| `test_condition_contains` | `code contains ERR` |
| `test_condition_none_always_true` | None → 总执行 |
| `test_action_log_event` | 最简单的 action |
| `test_action_update_wo_qty_increment` | 增量计数 |
| `test_action_update_wo_qty_absolute` | 绝对计数 |
| `test_action_set_wo_status` | 改 WO 状态 |
| `test_action_update_sn_status` | 改 SN 状态 |
| `test_action_create_defect` | 自动缺陷登记 |
| `test_action_webhook_forward_mocked` | mock httpx |
| `test_action_condition_not_met_skipped` | 条件不满足 → skipped |
| `test_action_unknown_type_error` | 未知 action → error |
| `test_persist_message_with_actions` | 端到端：收消息 → 解析 → action → 入库 |
| `test_persist_message_action_error_continues` | 一个 action 失败不影响其他 |
| `test_mapping_crud_pages` | Admin UI CRUD |

---

## 8. 任务拆分（预估 7 task）

1. **Migration + TopicMapping 模型 + MachineMessage 新字段** — ALTER TABLE + 新表
2. **MqttMessageParser** — parse + resolve_path + evaluate_condition（纯函数 TDD）
3. **ActionExecutor + 6 个 action handlers** — 每个 action 独立可测
4. **persist_message 增强** — 集成 parser + executor 到消息处理
5. **ConnectivityService 扩展** — TopicMapping CRUD
6. **Admin UI Mapping CRUD** — 详情页 + 路由 + 模板
7. **消息详情增强 + 回归 + memory**

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Action 执行失败影响监听器 | 独立 try/except，失败记录不中断 |
| parsed_data JSON 太大 | 独立 JSON 列；原始 payload 已截断 |
| JSONPath 漏洞 | 纯字符串操作（无 eval），路径不存在返 None |
| webhook_forward 超时 | httpx async + 10s timeout，失败记 error |
| Action commit 顺序 | Action 各自 commit；persist_message 只写 MachineMessage |
