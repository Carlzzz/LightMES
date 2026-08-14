# 设备运行管理模块设计

**日期**: 2026-08-14
**状态**: 设计已审阅，待转入实现计划
**作者**: Carlzzz + Claude（brainstorming session）

## 背景与动机

LightMES 当前的"设备"相关实体全部集中在 `connectivity` 模块，且只是**数采连接配置**（`MachineConnection` / `MqttConnection` / `OpcuaConnection` / `ModbusConnection` / `MachineTopic` / `MachineMessage` / `TopicMapping`）。它回答的是"怎么从设备读数据"，不回答"这台设备现在在干啥、停了多久、效率多少"。

参考项目 OpenMes（`C:\Users\zhaocao\Documents\GitHub\OpenMes`）提供了一套干净的"设备**运行**管理"——数采 + 状态时间切片 + 停机记录 + OEE 看板。本设计借鉴其核心抽象，适配 LightMES 的模块化单体架构。

**注意**：OpenMes 也**没有**设备资产台账、点检、保养、备件这些 EAM 维度。本设计同样不覆盖 EAM，留给未来 Phase。

## 范围

### In scope（Phase 1）

- 协议无关的"地址 → 信号语义"映射（`MachineTag`）
- 工位状态时间切片（`WorkstationState`）+ 状态机
- 自动停机记录（`ProductionDowntime`）+ 停机原因主数据（`DowntimeReason`）
- OEE 部分指标：**可用率 + 质量率**（OEE = A × Q，跳过性能率）
- 实时监控大屏（HTML 3s 轮询）
- 班长手动 transition + 停机原因人工修正
- 设备故障自动建 issue（**默认关闭**）

### Out of scope（明确不做）

- 性能率（OEE 的 P）—— 需要 `ideal_cycle_time` 主数据 + 实际节拍可测，主数据未准备好
- 设备资产台账（编号/型号/序列号/购入/价值）
- 点检任务、保养计划、故障维修工单（EAM）
- 备件库存
- WebSocket 实时推送（HTML 3s 轮询足够）
- 写回设备（Modbus/OPC-UA write）—— 安全敏感，延后
- 多设备聚合状态（一个工位多台主设备的"任一 FAULT → 工位 FAULT"聚合）—— Phase 1 约束一个 WorkStation 至多一个 active MachineConnection

## 架构（§1）

### 新模块

`src/lightmes/modules/equipment/`，与现有模块（auth/masterdata/production/issue/connectivity）平级。

### 模块内组件

| 文件 | 职责 |
|---|---|
| `models.py` | `MachineTag` / `WorkstationState` / `ProductionDowntime` / `DowntimeReason` |
| `tag_service.py` | MachineTag CRUD + `applyTransform(value_map/scale/offset)` |
| `state_machine.py` | `WorkstationStateMachine.transition()`：关旧行 + 开新行 + 自动开/关 ProductionDowntime |
| `ingestor.py` | `MachineSignalIngestor.ingest(tag, raw)`：按 `signal_type` 路由 |
| `downtime_service.py` | ProductionDowntime CRUD + 人工修正原因 |
| `oee_service.py` | OEE 读模型：可用率 + 质量率 |
| `monitor_service.py` | 实时监控读模型：当前 state、今日可用率、good/reject 计数 |
| `events.py` | `MachineSignalReceived` 事件定义 + listener 注册 |
| `router.py` | HTML 路由 + JSON API |
| `schemas.py` | Pydantic 输入/输出 schema |
| `__init__.py` | `register(app)` 入口 |

### 依赖方向（无循环）

```
masterdata ──┐
production ──┼──► equipment
issue ───────┘       ▲
                     │ (in-process 事件订阅)
connectivity ────────┘
```

connectivity 通过**事件总线**转发信号给 equipment，不直接 `import equipment`，避免循环依赖。equipment 也不读 `MachineMessage`（保持纯业务层）。

### 事件总线

复用 `production/events.py` 已有的 in-process 同步派发模式。connectivity 的 `action_executor` 在执行 TopicMapping 后，对命中的 `MachineTopic` 关联的所有 `MachineTag`：

1. 用 `tag.field_path`（JSONPath）从已解析 payload 提取原始值
2. 调 `tag_service.applyTransform(tag, raw)` 得到语义值
3. publish `MachineSignalReceived(tag_id, value, message_id)`

equipment 启动时注册 listener，调 `MachineSignalIngestor.ingest()`。

### TopicMapping 与 MachineTag 的关系

**两者完全正交、互不干扰**：

- `TopicMapping`：现有概念，"一条消息触发什么业务动作"（`log_event` / `update_work_order_produced_qty` / `set_work_order_status` / `update_serial_unit_status` / `create_defect` / `webhook_forward`）。**完全不动。**
- `MachineTag`：新概念，"一条消息的某个字段是什么信号语义"（`state` / `good_count` / ...）。

一条消息进系统后，两条管道并行：

1. parser 解析 payload
2. 所有 `TopicMapping` 走 `action_executor`（业务动作）—— 现状不变
3. 所有 `MachineTag` 走新的 ingestor（信号语义）—— 新增

## 数据模型（§2）

### 新建 4 张表

**1. `machine_tags`** — 协议无关地址 → 信号映射

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `machine_topic_id` | FK → `machine_topics`, CASCADE, index | 订阅源（复用 connectivity） |
| `name` | str(100) | 人读名 |
| `field_path` | str(255) | payload 内 JSONPath，复用 `connectivity/parser.py` 提取器 |
| `signal_type` | str(20) + CK | `state`/`good_count`/`reject_count`/`cycle_complete`/`telemetry`/`alarm` |
| `data_type` | str(20)? | `int16`/`uint16`/`int32`/`uint32`/`float32`/`bool`/`json` |
| `transform` | JSON? | `{value_map: {"1":"RUNNING","2":"IDLE"}, scale: 0.1, offset: -50}` |
| `unit` | str(20)? | telemetry 单位 |
| `last_count_value` | int? | 仅 good_count/reject_count：上次累积值，用于算 delta |
| `is_active` | bool | |

唯一约束 `(machine_topic_id, field_path, signal_type)`。

**2. `workstation_states`** — 状态时间切片

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `work_station_id` | FK → `work_stations`, index | |
| `state` | str(20) + CK | `RUNNING`/`IDLE`/`STOPPED`/`FAULT`/`SETUP`/`WAITING`/`CLEANING`/`MAINTENANCE` |
| `started_at` | DateTime(tz) | |
| `ended_at` | DateTime(tz)? | NULL = 当前 open |
| `duration_seconds` | int? | `ended_at` 时回填 |
| `source` | str(20) | `machine` / `manual` |
| `metadata` | JSON? | telemetry + alarm 详情 |

索引：`(work_station_id, started_at)`、`(work_station_id, ended_at)`。

**3. `production_downtimes`** — 停机记录

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `line_id` | FK → `lines`, index | 冗余，便于产线汇总 |
| `work_station_id` | FK → `work_stations`, index | |
| `downtime_reason_id` | FK → `downtime_reasons`? | NULL = 等待人工修正 |
| `started_at` | DateTime(tz) | |
| `ended_at` | DateTime(tz)? | |
| `duration_minutes` | int? | `ended_at` 时回填 |
| `notes` | Text? | 自动 reason 时写 `Auto-recorded from machine state FAULT` |
| `is_planned` | bool | 冗余（来自 `reason.kind`），便于查询 |

**4. `downtime_reasons`** — 停机原因主数据

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `code` | str(50) + UQ | 如 `AUTO-FAULT`、`TOOL-CHANGE`、`MATERIAL-SHORTAGE` |
| `name` | str(200) | |
| `kind` | str(20) + CK | `planned` / `unplanned` |
| `is_active` | bool | |
| `is_system` | bool | AUTO-* 不可删 |

系统默认值（lifespan ensure，复用 `DefectService.ensure_system_defect_types` 已有模式）：

| code | kind | 触发状态 |
|---|---|---|
| `AUTO-FAULT` | unplanned | FAULT |
| `AUTO-STOP` | unplanned | STOPPED |
| `AUTO-WAIT` | unplanned | WAITING |
| `AUTO-CLEAN` | planned | CLEANING |
| `AUTO-MAINT` | planned | MAINTENANCE |

### 现有表改动

- **`machine_connections`**：加 `work_station_id: int? FK → work_stations, index`（nullable，配置可以先于挂工位）
- **`topic_mappings`**：**不动**（业务动作语义独立）
- **`work_stations`**：**不动**（不加"当前状态"缓存字段，靠 `WorkstationState` open 行查询）

### 状态分类（参考 OpenMes `WorkstationState`）

```python
LOSS_STATES     = [STOPPED, FAULT, WAITING]      # unplanned availability loss
PLANNED_STATES  = [CLEANING, MAINTENANCE]        # scheduled, planned downtime
DOWNTIME_STATES = LOSS_STATES + PLANNED_STATES   # all states that auto-open downtime
RUNNING_STATES  = [RUNNING, IDLE, SETUP]         # production-effective, no downtime
```

### Migration

一个 Alembic revision：

1. 建 4 张新表（含所有索引、CK、UQ）
2. `machine_connections` 加 `work_station_id` 列 + FK + index
3. downgrade 反向

系统默认 DowntimeReason（5 个 AUTO-*）在 lifespan 启动时 ensure（不在 migration 里写数据）。

## 数据流（§3）

### 完整信号管道

```
[设备] ──MQTT/OPC-UA/Modbus──► [connectivity/mqtt_listener]  (已有)
                                       │
                                       ▼ persist
                                [machine_messages]            (已有)
                                       │
                                       ▼ parser 解析
                                [parsed JSON]
                                       │
                       ┌───────────────┴────────────────┐
                       ▼                                ▼
              [action_executor]                  [NEW: tag extractor]
              (已有, 业务动作)                   (新: 遍历该 topic 关联的 MachineTag)
                       │                                │
                       │ topic_mappings                 │ 对每个 tag:
                       │ condition → action             │  1. JSONPath 提取 field_path
                       │ log_event /                    │  2. applyTransform
                       │ update_wo_qty /                │  3. publish MachineSignalReceived
                       │ create_defect / ...            ▼
                       ▼                       [equipment/events bus]
                (业务效果)                            │
                                                       ▼
                                            [MachineSignalIngestor]
                                            按 signal_type 路由:
                                                       │
              ┌────────────────┬────────────┬─────────┴┬──────────────┐
              ▼                ▼            ▼          ▼              ▼
          state          good_count   reject_count  telemetry       alarm
              │            (累积→delta)(累积→delta)  (写 state.metadata) (可选建 issue)
              ▼                │            │
    [WorkstationStateMachine.transition]
       1. SELECT FOR UPDATE 锁当前 open state 行
       2. 关旧 open state 行
       3. 开新 state 行
       4. 若进入 DOWNTIME_STATES:
            自动开 ProductionDowntime
            reason = AUTO-FAULT/STOP/WAIT/CLEAN/MAINT
          若离开 DOWNTIME_STATES: 关 open Downtime
              │
              ▼
    [workstation_states]  [production_downtimes]
              │                    │
              └─────────┬──────────┘
                        ▼
              [equipment/oee_service]
              读模型(无副作用):
              - 可用率 A = 班次时长 - downtime / 班次时长
              - 质量率 Q = (produced - scrapped) / produced
                          (WorkOrder.produced_qty + DefectRecord)
              - OEE = A × Q   (跳过 P)
                        │
                        ▼
              [equipment/router HTML/JSON]
              - /equipment/monitor  (3s 轮询大屏)
              - /equipment/downtimes
              - /equipment/oee
              - /equipment/downtime-reasons (CRUD)
              - /equipment/tags (CRUD)
```

### 关键决策

1. **good_count/reject_count 的 delta 计算**：设备发的是累积值。`MachineTag.last_count_value` 存上次值，每次 ingest 计算 `delta = current - last`，更新 last。Phase 1 这些计数**仅用于实时显示**，不参与 OEE。

2. **OEE 质量率走 `WorkOrder.produced_qty + DefectRecord`**（过站闭环数据），不走设备计数。设备计数可能不准（漏过站、重复计数），过站记录是质量闭环数据。

3. **OEE 时段口径**：用 LightMES 已有的 `ShiftService.current_at()` 定位班次。班次外的"非计划生产时间"不计入分母。Phase 1 提供"按自然日"和"按班次"两种视图。

4. **班长人工干预**：
   - monitor 大屏工位卡片有"切换状态"下拉 → `state_machine.transition(ws, new_state, source='manual')`
   - 停机列表可编辑 `downtime_reason_id`（把 AUTO-FAULT 修正为 `TOOL-WEAR` 等）

5. **WorkstationState 没有"当前状态"缓存字段**：所有"当前状态"查询走 `WHERE work_station_id=X AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1`。天然支持历史回放和快照重构。

6. **故障自动建 issue 默认关闭**：在 `lightmes.config.Settings` 加 `equipment_auto_create_issue_on_fault: bool = False`（沿用 LightMES 现有 Settings 模式，不另建 EquipmentSettings 表）。开启时信号 `alarm` 或 state→FAULT 自动建 issue，`source='equipment_alarm'`。

## 错误处理（§4）

| 场景 | 处理 |
|---|---|
| **设备断连（信号停了）** | 不主动转 STOPPED。`MachineConnection.status` 已维护连接状态，monitor 大屏显示"连接断"banner，WorkstationState 保持最后一个状态。班长手动 transition 才转 STOPPED。**理由**：连接断 ≠ 设备停。 |
| **value_map 没匹配的原始值** | tag.transform 的 value_map 有 `default` 走 default；没有 default 时**记录到 `machine_messages.processing_error` + `logger.warning`**，不抛异常。state 保持上一个。 |
| **good_count 回退（设备重启清零）** | ingestor 检测 `current < last_count_value` → 视为设备重启，**重置 last_count_value = current**，delta=0，`logger.info("counter reset")`。不抛异常。 |
| **同一信号短时间重复 ingest** | state 信号：`transition()` 已有"new_state == current_state 时 no-op + metadata 合并"逻辑。count 信号：delta 自然为 0，无害。 |
| **并发 transition** | `transition()` 内部用 `SELECT FOR UPDATE` 锁当前 open state 行（PostgreSQL）。第二个 transition 等第一个 commit 后看到新 state，no-op 或再次 transition。 |
| **OPC-UA / Modbus daemon 挂了** | Runtime visibility 硬规则：supervisor heartbeat 已有，UI 显示"not running"banner + 复制命令。Phase 1 不做自动重启。 |
| **人工修正越权** | `/equipment/downtimes/{id}/reason PATCH` 要求 supervisor/admin 角色（`html_role_guard("supervisor", "admin")`）。operator 只读。 |
| **删除有引用的 DowntimeReason** | 系统级（`is_system=True`，AUTO-*）禁止删除；非系统但有 `ProductionDowntime` 引用时禁止删除（业务错误"原因已被 N 条停机记录使用"）。改为 `is_active=False` 软停用。 |
| **MachineConnection 解绑 WorkStation** | 允许解绑，已采集的 WorkstationState/Downtime 保留。ingestor 检查到 `connection.work_station_id IS NULL` 时跳过信号处理 + `logger.warning`。 |
| **count 信号但 tag 没配 data_type** | applyTransform 时若 raw 是字符串、需要数值，做 `float(raw)`，失败则丢弃该次 + `logger.warning`。 |

**整体哲学**：信号管道绝不抛异常中断主流程。怪值/缺值都降级处理 + 日志。和 OpenMes 一致。

## 测试策略（§5）

复用 LightMES 已有的 pytest + PostgreSQL 测试基线（`tests/modules/equipment/`）。

### Unit tests

- `test_tag_transform.py`: value_map + default、scale/offset、字符串原始值转数值
- `test_state_machine.py`: 关旧开新、相同状态 no-op、并发（用 `time.sleep` 模拟竞争，验证 FOR UPDATE）、自动开/关 ProductionDowntime（LOSS/PLANNED/离开 各路径）
- `test_ingestor.py`: 6 种 signal_type 各自路由正确、good_count delta 计算 + 重置场景

### Integration tests

`test_signal_pipeline.py`: 端到端——发一条 MQTT 消息 → parser → tag extractor → event → ingestor → `WorkstationState` 写入 + `ProductionDowntime` 自动开/关。同一消息被 `TopicMapping` 和 `MachineTag` 同时消费（业务动作 + 信号语义都触发）。

### OEE 计算测试

`test_oee_service.py`: fixture——班次 8 小时 + 1 小时 unplanned downtime + 0.5 小时 planned + `WorkOrder.produced_qty=100` + `DefectRecord` scrapped=5。断言：可用率 = (8-1)/8 = 87.5%，质量率 = 95/100 = 95%，OEE ≈ 83.1%。边界：班次内无 downtime、班次外 downtime（不计入）、跨班次 downtime。

### Migration 测试

`test_migration.py`: upgrade 后四张表存在 + `machine_connections` 有 `work_station_id` 列。系统 DowntimeReason ensure 后有 5 个 AUTO-* 记录。

### UI tests

`test_pages.py`:
- `/equipment/monitor` 200 + 显示工位卡片
- `/equipment/downtimes` 列表 + 人工修正 reason（admin 可、operator 拒）
- `/equipment/tags` CRUD（admin 可、operator 只读）
- Runtime visibility banner 渲染（连接 active 但 daemon 未跑 → amber banner）

### 不测的

- WebSocket（没做）
- 性能率（跳过了）
- OPC-UA 真实连接（sidecar 自身不在 CI 覆盖，和 OpenMes 一致）

## 与 OpenMes 的对照

### 借鉴（直接照搬）

| 概念 | OpenMes | LightMES |
|---|---|---|
| 协议无关 tag 抽象 | `MachineTag` (signal_type + transform) | 完全照搬 |
| 状态时间切片 | `WorkstationState` (started_at/ended_at) | 完全照搬 |
| 状态分类 | LOSS_STATES / PLANNED_STATES / DOWNTIME_STATES | 完全照搬 |
| 自动停机 reason | AUTO-FAULT / AUTO-STOP / ... | 完全照搬 |
| WorkstationStateMachine.transition | 关旧开新 + 自动开/关 downtime | 完全照搬 |
| Runtime visibility 硬规则 | heartbeat + UI banner | 完全照搬 |
| source = 'machine' / 'manual' | 区分自动 vs 人工 | 完全照搬 |

### 偏离（适配 LightMES）

| 决策 | OpenMes | LightMES | 理由 |
|---|---|---|---|
| Tag 挂在哪 | `tag.workstation_id` 直接挂 | `tag.machine_topic_id` + `connection.work_station_id` 链路 | LightMES 已有 `MachineTopic` 订阅抽象，不重复造 |
| 信号转发机制 | `MachineSignalIngestor::ingest` 直接调用 | 事件总线 `MachineSignalReceived` | LightMES 是模块化单体，connectivity 不应 import equipment |
| TopicMapping | OpenMes 没有 | LightMES 已有，**完全保留** | 业务动作（log_event / update_wo_qty）独立价值，不污染 |
| 协议适配 | 内置 PHP daemon + 外部 sidecar | 已有 MQTT listener + OPC-UA + Modbus | connectivity 模块已完成，不重写 |
| 实时性 | HTTP 3s 轮询 | 同 | 一致 |
| OEE 范围 | 完整 A×P×Q | A×Q（跳过 P） | 主数据 `ideal_cycle_time` 未准备，Phase 1 不做 |

## 实现分阶段建议

虽然本设计是 Phase 1 一体化交付，但实现顺序建议（供后续 writing-plans 参考）：

1. **数据层**：models + migration + lifespan ensure DowntimeReason
2. **核心服务**：`tag_service` → `state_machine` → `ingestor`（依赖前者）
3. **事件桥**：events + connectivity/action_executor 改造
4. **读模型**：`downtime_service` → `oee_service` → `monitor_service`
5. **UI**：tags CRUD → monitor 大屏 → downtimes 列表 → oee 看板 → downtime-reasons CRUD
6. **可选**：故障自动建 issue（默认关闭）
7. **测试**：随每层一起写，integration test 在第 3 层后补

## 未决事项（Phase 2 候选）

- 性能率（OEE 的 P）：等 Operation 或 Product 加 `ideal_cycle_time_seconds` 字段后
- 多设备聚合（一个工位多台主设备）
- 设备资产台账 + 点检 + 保养（EAM 维度）
- WebSocket 实时推送
- 设备写回（安全敏感）
- 跨班次 downtime 加权计算
