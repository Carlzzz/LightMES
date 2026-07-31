# LightMES 设计文档（总体架构 + MVP 详细设计）

- 日期：2026-07-31
- 状态：设计已获用户口头确认，待书面复核
- 作者：单人开发者 + Claude 协作

---

## 1. 背景与目标

为笔记本外壳组装线开发一套**轻量级 MES**。开发资源为单人 + AI 协作，开发者具备深厚的 MES 实施/运维/二次开发经验，熟悉 Python 与 C#，但首次进行从 0 到 1 的完整工程开发。

系统最终要覆盖四大能力域：

1. **金蝶 ERP 集成** —— 工单下发接收，报工回传
2. **数据采集平台** —— 边缘网关实时采集车间设备工艺参数/质量/过站数据，协议以 OPC UA / Modbus / MQTT 为主
3. **完整 MES 业务** —— 生产执行、质量管理、物料管理、设备管理、追溯管理
4. **丰富的 API 生态** —— 为后续 MCP Server 与 AI Agent 集成打基础

### 设计基调
- **轻量、单人可维护优先**：架构复杂度必须匹配单人团队。宁可后期再拆，不可前期过度设计。
- **API-first**：所有业务能力先做 API，Web 页面只是消费者之一。这直接服务于第 4 项诉求。
- **YAGNI**：每一期只做当期必需，明确划出不做清单。

---

## 2. 关键约束（已确认）

| 约束 | 决策 |
|---|---|
| 部署形态 | 厂内服务器跑 MES；独立**边缘采集网关**贴近设备 |
| 接入规模 | 至少 50 个设备/工位 |
| 采集强度 | 高频大量（部分点位 10Hz+、波形、能耗曲线），需时序库 |
| 数据用途 | 既做实时监控告警，又做追溯 |
| 主数据库 | PostgreSQL |
| 时序数据库 | **TimescaleDB**（PG 扩展，单库统一，业务表与时序表可 JOIN） |
| 采集实现 | 混合：标准协议用开源中间件（Telegraf/Node-RED），老设备自研采集器 |
| 现场设备 | 标准 OPC UA 与老旧设备混合 |
| ERP | 金蝶在用，版本未定（P2 前需确认） |

---

## 3. 技术栈（已确认：Python 单栈）

| 层 | 选型 | 理由 |
|---|---|---|
| MES 后端 | **Python + FastAPI** | 异步、自动生成 OpenAPI（天然喂给 MCP/AI）、单人心智负担低 |
| 数据层 | **SQLAlchemy + Alembic** | 成熟 ORM + 数据库迁移 |
| 前端 | **HTMX + Jinja2**（服务端渲染）为主 | 车间看板/扫码页面无需重前端；单人无需维护独立 SPA 工具链 |
| 数据库 | **PostgreSQL + TimescaleDB 扩展** | 单库统一，业务/时序共存可 JOIN |
| 消息 | **MQTT（Mosquitto）** | 设备数据统一入口，边缘与 MES 解耦 |
| 采集边缘 | **Telegraf**（标准协议）+ **Python 采集器**（老设备）；**C# 采集器**作为私有协议逃生舱，经 MQTT 回传 | 能配置解决的不写代码；C# 仅用于其 OPC UA SDK 成熟度确有优势的死角 |
| 依赖/打包 | **uv**（或 poetry）+ **Docker Compose** | 单人友好、环境一致 |
| 测试 | **pytest** | Python 标准 |

**逃生舱原则**：主栈坚持 Python 统一；个别啃不动的老设备协议，允许独立 C# 小采集器把数据吐到 MQTT，不侵入 MES 主栈。

---

## 4. 总体架构：模块化单体（Modular Monolith）

**不采用微服务**。单人团队下微服务的部署/通信/分布式事务/可观测性成本是灾难。采用单一部署单元、内部清晰分模块，将来某模块确需独立再拆。

```
┌─────────────────────────────────────────────────────────────┐
│                 厂内服务器 (Docker Compose)                    │
│   ┌─────────────────────────────────────────────────┐        │
│   │            LightMES 应用 (FastAPI 单体)            │        │
│   │   接入层:  REST API  +  Web页面(HTMX/Jinja2)      │        │
│   │   ──────────────────────────────────────────     │        │
│   │   业务模块 (内部边界清晰, 各自独立):               │        │
│   │     • production  生产执行 (工单/排产/派工/报工/WIP)│        │
│   │     • quality     质量 (检验/SPC/不良)            │        │
│   │     • material    物料 (防错/批次/条码)           │        │
│   │     • equipment   设备 (采集接入/OEE/点检)         │        │
│   │     • trace       追溯 (履历/物料谱系/工艺参数)     │        │
│   │     • integration ERP集成 (金蝶适配器)            │        │
│   │   ──────────────────────────────────────────     │        │
│   │   共享内核:  领域模型 / 认证 / 事件总线(库内)      │        │
│   └─────────────────────────────────────────────────┘        │
│                          │                                    │
│   ┌──────────────────────┴──────────────────────┐            │
│   │   PostgreSQL + TimescaleDB (单库)             │            │
│   │   业务表(关系) + 时序表(工艺参数/采集)          │            │
│   └───────────────────────────────────────────────┘           │
│                          ▲ MQTT (设备数据统一入口)             │
└──────────────────────────┼────────────────────────────────────┘
                           │
        ┌──────────────────┴───────────────────┐
        │        边缘采集网关 (车间, 贴近设备)     │
        │   Telegraf + Python采集器 + [可选]C#采集器 │
        └────────────────────────────────────────┘
                           ▲  现场设备 (50+)
```

### 关键设计点

1. **MQTT 作为设备数据统一入口**：无论 Telegraf、Python 采集器还是 C# 逃生舱，都把数据发到 MQTT Broker。MES 只订阅 MQTT，不关心采集方式。边缘与 MES 彻底解耦，加设备/换采集方式不动 MES。（MVP 不落地，P3 启用；P0 先把 Broker 与订阅骨架搭好。）

2. **模块间通过库内事件总线通信**，而非直接互调。如"过站完成"事件，trace 与 quality 均可订阅。保持模块边界干净，是 API 生态与将来拆分的基础。

3. **API-first**：所有业务能力先做 REST API（FastAPI 自动生成 OpenAPI）。Web 页面只是 API 的消费者之一。P6 的 MCP Server 直接读这份 OpenAPI，AI 集成无需重写业务逻辑。

### 模块边界原则
- 每个模块有独立目录，对外暴露服务接口（service layer），内部实现（models/repository）不被其他模块直接引用。
- 跨模块协作优先走事件总线；确需同步调用时，只调对方 service 层公开接口。
- 共享内核（shared kernel）只放真正通用的东西：基础领域类型、认证、事件总线、数据库会话管理。

---

## 5. 分期路线（Roadmap）

每一期都是独立的 spec → plan → 实现循环。**不一次全做，一期一期长肉。**

| 期 | 名称 | 交付物 | 依赖 |
|---|---|---|---|
| **P0** | 工程地基 | 项目骨架、Docker Compose(PG+Timescale+MQTT)、核心领域模型、认证、pytest+CI、一个端到端竖切(API+页面) | 无 |
| **P1 (MVP)** | 过站追溯主线 | 条码→过站扫码→WIP流转→过站履历+物料谱系(正/反查)。手工/扫码驱动 | P0。**现场立即可用** |
| **P2** | ERP 工单闭环 | 金蝶工单下发→接收→派工→报工→回传 | P1；需先定金蝶版本 |
| **P3** | 设备采集 | 边缘网关→MQTT→TimescaleDB→实时监控看板→工艺参数追溯 | P0 的 MQTT/时序底座 |
| **P4** | 质量深化 | 来料/过程检验、SPC、不良管理 | P1、P3 |
| **P5** | 设备管理深化 | OEE、点检 | P3 |
| **P6** | AI/MCP 层 | MCP Server(读 OpenAPI)、AI Agent 集成 | 前期 API 生态 |

**当前范围仅 P0 + P1。** P2 之后可等（ERP 版本未定正好不急）。因全程 API-first，P6 水到渠成而非大改。

---

## 6. MVP（P0 + P1）详细边界

### P0 工程地基 —— 做
- 项目结构（模块化单体骨架）、依赖管理（uv）、配置管理（环境变量/配置文件）
- Docker Compose：PostgreSQL+TimescaleDB、Mosquitto(MQTT)、应用容器
- 核心领域模型 + Alembic 迁移基线
- 简单认证（本地账号/登录，先不接 LDAP）
- 库内事件总线骨架
- pytest 测试框架 + 基础 CI
- 一个端到端竖切：一个可跑 API + 一个可看页面，证明骨架贯通

### P1 过站追溯 —— 做
- **主数据**：产品、工艺路线（工序/工位序列）、工位、**BOM**（成品→应含组件，标明批次料/唯一件）
- **工单**：手工创建（P2 才从 ERP 来）
- **条码**：SN 生成与绑定，**编码规则可配置**（规则驱动生成器，非硬编码）
- **过站**：工位扫码报工——校验工艺路线顺序（防跳站/防重复）、记录时间/操作员/工位
- **WIP**：实时在制品状态（每个 SN 当前在哪站、什么状态）
- **追溯**：
  - 过站履历（SN 走过哪些站、何时、谁）
  - **物料谱系**：批次组件（按批次号）+ 唯一组件（按组件 SN）绑定到成品 SN；**正向查**（成品→组件）+ **反向查**（组件→成品）
- **返工/拆解**：谱系绑定可逆（解绑组件、SN 重新走工艺路线返工），历史保留可查

### MVP 明确不做（YAGNI）
- 不接 ERP、不接设备采集
- 不做 SPC、OEE、点检
- 不做排产算法（工单手工排）
- **不做完整批次管理**（批次库存/收发存/FIFO/有效期告警 → P4）。MVP 中批次号只是绑定时录入/扫入的标识，非库存系统
- 防错料仅做最基础的"组件类型是否符合 BOM"校验，复杂防错规则留后
- 不做复杂权限（先够用）

---

## 7. 核心数据模型（MVP）

以下为概念模型，字段为关键项而非最终建表清单。命名用英文（表名），中文注释说明用途。所有表默认含 `id`（主键）、`created_at`、`updated_at`。

### 7.1 主数据

**product（产品/物料主数据）**
- `code` 物料编码（唯一）、`name` 名称、`type` 类型（成品/半成品/组件/辅料）
- `spec` 规格、`unit` 单位
- `track_mode` 追踪方式：`serial`（唯一件，按 SN）/ `batch`（批次料，按批次号）/ `none`（不追踪）
- 说明：product 既表示成品，也表示要绑定的组件；`track_mode` 决定它作为组件时按 SN 还是批次绑定。

**station（工位）**
- `code`、`name`、`description`
- `location` 车间位置（可选）

**routing（工艺路线）**
- `code`、`name`、`product_id`（关联成品）、`version`、`status`（生效/停用）
- 一个成品可有多个版本路线，同时只一个生效。

**routing_step（工序/路线步骤）**
- `routing_id`、`seq`（顺序号，决定过站顺序）、`station_id`、`name` 工序名
- `is_mandatory` 是否必过（返工场景可能跳过部分）
- 说明：过站顺序校验的依据。

**bom（物料清单头）**
- `product_id`（成品）、`version`、`status`

**bom_item（BOM 行）**
- `bom_id`、`component_product_id`（组件，指向 product）、`qty` 用量
- `track_mode` 继承自组件 product（冗余存便于校验）：`serial` / `batch`
- 说明：谱系绑定时的基础校验依据——绑定的组件类型必须在 BOM 内。

### 7.2 生产与在制

**work_order（工单）**
- `code` 工单号（唯一）、`product_id`、`routing_id`、`qty` 计划数量
- `status`：`created`/`released`/`in_progress`/`completed`/`closed`
- `source`：`manual`（MVP）/ `erp`（P2 预留字段）
- `planned_start`、`planned_end`（手工填，MVP 无排产算法）

**serial_unit（产品单元 / SN）**
- `sn` 序列号（唯一）、`work_order_id`、`product_id`
- `status`：`created`/`in_process`/`finished`/`scrapped`/`reworking`
- `current_step_seq` 当前所在工序序号、`current_station_id` 当前工位
- 说明：WIP 实时状态即由本表 `status`+`current_*` 表达，无需独立 WIP 表。

**sn_rule（SN 编码规则，可配置）**
- `code`、`name`、`product_id`（可空=通用规则）
- `pattern` 规则模板（如 `{PREFIX}{YYMMDD}{SEQ:5}`）
- `prefix`、`seq_reset`（流水重置周期：日/月/永不）、`current_seq`
- 说明：规则驱动的 SN 生成器读取本表，避免硬编码。

### 7.3 过站与追溯

**station_pass（过站记录 / 履历）**
- `serial_unit_id`、`work_order_id`、`routing_step_id`、`station_id`
- `operator_id`、`pass_time`、`result`：`pass`/`fail`
- `remark`
- 说明：产品履历 = 某 SN 的全部 station_pass 按时间排列。

**genealogy_bind（物料谱系绑定）**
- `parent_sn_id`（成品 serial_unit）、`bom_item_id`（对应 BOM 行，可空以容错）
- `component_type`：`serial` / `batch`
- `component_sn`（唯一件时填组件 SN 字符串）/ `component_batch_no`（批次料时填批次号）
- `component_product_id`（组件物料）、`qty`
- `bind_time`、`operator_id`、`station_pass_id`（在哪次过站绑定的）
- `status`：`active` / `unbound`（拆解后置为 unbound，不物理删除）
- `unbind_time`、`unbind_reason`（拆解/返工时记录）
- 说明：
  - **正向查**：`WHERE parent_sn_id = ?` → 该成品的所有组件
  - **反向查**：`WHERE component_sn = ?` 或 `component_batch_no = ?` → 装入了哪些成品
  - **可逆**：拆解时置 `status=unbound` 保留历史，不删除。

### 7.4 认证（MVP 最简）

**user（用户）**
- `username`（唯一）、`password_hash`、`display_name`、`role`（先枚举：`admin`/`operator`）、`is_active`
- 说明：MVP 只做本地账号 + 登录会话；`operator_id` 等外键指向本表。

---

## 8. 关键业务流程

### 8.1 过站流程
1. 操作员在工位扫描成品 SN（或工单首站生成 SN）
2. 系统校验：SN 存在、工单在制、当前工序 = 该工位对应的路线步骤（防跳站）、该步骤未重复过站（防重复）
3. 如该工序需绑定组件：扫描组件 SN / 批次号 → 校验组件类型在 BOM 内 → 写 `genealogy_bind`
4. 写 `station_pass`；更新 `serial_unit.current_step_seq`/`current_station_id`
5. 发布 `StationPassed` 事件（trace 等模块订阅）
6. 若为末工序，`serial_unit.status = finished`

### 8.2 返工/拆解流程
1. 判定某 SN 需返工（末检不良或过程判废）
2. 置 `serial_unit.status = reworking`
3. 如需更换组件：将相关 `genealogy_bind` 置 `unbound` 并记 `unbind_reason`
4. 依返工路线（MVP 可简化为回退到指定工序序号）重置 `current_step_seq`
5. 重新过站时按正常流程绑定新组件、写新 `station_pass`
6. 全程历史保留，追溯可见"换过什么、何时、为何"

### 8.3 追溯查询
- **产品履历**：给 SN → 该 SN 的 station_pass 时间线 + active/历史 genealogy_bind
- **物料正向**：给成品 SN → 全部组件（SN/批次）
- **物料反向**：给组件 SN 或批次号 → 装入的全部成品 SN（含已拆解的历史，标注状态）

---

## 9. 库内事件（MVP 起步集）

事件总线为进程内同步/异步分发（MVP 用简单同步分发即可，接口预留异步扩展）。起步事件：
- `WorkOrderReleased`、`SerialUnitCreated`、`StationPassed`、`GenealogyBound`、`GenealogyUnbound`、`SerialUnitFinished`、`SerialUnitScrapped`

订阅示例：trace 模块订阅 `StationPassed` 更新履历视图；将来 quality 订阅 `SerialUnitFinished` 触发末检。

---

## 10. 错误处理与校验

- **过站校验失败**（跳站/重复/工单未发布/SN 不存在）：返回明确错误码 + 中文提示，前端可读；不写库。
- **谱系绑定失败**（组件不在 BOM / 批次号为空 / 唯一件 SN 已被别的成品占用）：拒绝并提示。
- **并发**：同一 SN 的过站操作用行级锁或乐观锁（`serial_unit` 版本号）防止双扫。
- **边界校验只在系统入口做**（API 层校验入参），内部模块间信任。

---

## 11. 测试策略

- **TDD 优先**：核心领域逻辑（过站校验、SN 生成、谱系正反查、返工拆解）先写测试。
- **集成测试用真实 PostgreSQL**（Docker 起测试库），不 mock 数据库——避免 mock 与真实 SQL 行为分歧。
- **测试分层**：领域逻辑单测、repository/DB 集成测试、API 端到端测试。
- 每个 P0/P1 交付竖切都要有对应测试并全绿方可视为完成。

---

## 12. 待办 / 开放问题（不阻塞 MVP）

- **金蝶版本**：P2 前必须确认（云星空 / K3 / 星辰），决定集成方式（OpenAPI / WebAPI / 数据库对接）。
- **时序库压测**：P3 前对 TimescaleDB 在 50 设备高频写入下压测，确认是否够用（预期够，留观测）。
- **前端形态**：若车间看板交互变复杂，P3/P4 可局部引入轻量 Vue，不推翻 HTMX 主体。
- **SN 规则模板语法**：`pattern` 的占位符语法细节（日期/流水/校验位）在 P1 实现时敲定。
- **返工路线**：MVP 简化为"回退到指定工序序号"；复杂返工路线（独立返工工艺）留后。

---

## 13. 下一步

本 spec 覆盖总体架构 + P0/P1（MVP）详细设计。经用户书面复核后，进入 **writing-plans**，为 **P0 工程地基** 编写实现计划（P0 是一切的底座，先做）。P1 待 P0 骨架跑通后另立计划。
