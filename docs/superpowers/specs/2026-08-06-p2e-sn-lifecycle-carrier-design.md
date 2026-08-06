# P2e 设计文档：SN 生命周期重构 + 载体码过站

- 日期：2026-08-06
- 状态：设计已获用户确认
- 上游：`2026-08-05-p2d-operator-station-design.md`（工位作业主界面）、P2a 层级/追溯、P2c 技能
- 定位：把 SN 从"首次过站惰性生成"重构为"下达时预生成"，并引入载体码（托盘/来料唯一码）作为 SN 标签打印前的过渡标识。落实"唯一码是一切的开始"。

---

## 1. 背景与目标

现状（P2d 及之前）：SN 在首次过站时惰性生成（`SnGenerator.next_sn`），首站扫的是**工单号**、之后才扫 SN——第一道工序交互与后续不一致，SN 不是统一起点。

P2e 重构 SN 生命周期为：
1. **下达即预生成**：工单下达时按 qty 批量预建 SerialUnit（状态 pending），SN 号码此刻确定，可批量打印。
2. **首站顺序投产**：首站先选工单、再扫载体码，系统按顺序取下一个 pending SN 赋给该载体码，建立绑定并过首工序。
3. **载体码过渡标识**：SN 标签打印前，后续站扫**载体码**过站；打印贴标后扫 **SN** 过站。系统自动识别扫的是 SN 还是载体码。
4. **解绑功能**：载体码绑定后一直跟随，提供解绑动作，解绑后关系清除、载体码可复用。
5. **极简扫码页下线**：`/production/scan` 从导航移除，工位作业成为唯一操作员入口。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 载体码性质 | 通用（物料码/托盘码不分类型）；绑定后一直跟随；提供**解绑**动作，解绑后关系清除。 |
| 2 | 预生成时机 | **工单下达（release）时**一次性按 qty 预生成全部 SerialUnit，状态 `pending`。 |
| 3 | 首站赋值 | **扫载体码自动取下一个 pending SN**（按 SerialUnit id 升序取，保证顺序），绑定 + 过首工序，一步完成。 |
| 4 | 扫码识别 | **自动识别**：先按 SN 查，查不到再按 carrier_code 查其绑定的 SerialUnit。操作员无需区分。 |
| 5 | 打印状态 | **不建打印状态**。SN 预生成后即可扫 SN 过站，与载体码并存；"何时打印"是现场行为，系统不管。 |
| 6 | 载体码唯一性 | **同时唯一、解绑后可复用**：活跃 SerialUnit 上 carrier_code 部分唯一索引；解绑置空即释放。 |
| 7 | 首站工单上下文 | **先选工单**锁定投产对象，再连续扫载体码；每扫一个从该工单取下一 pending SN。 |
| 8 | 数量校验 | 工单 pending SN 用完 → 提示"已全部投产，请选择新工单"；下达要求 qty>0 且工单已配置 SN 规则。 |
| 9 | 架构 | 方案 A：下达时批量预建 SerialUnit（唯一身份载体）；carrier_code 存 SerialUnit 字段（直读）+ 独立 carrier_binding 历史表（追溯）。 |

---

## 3. 数据模型

**SerialUnit 变更**（`production/models.py`）：
- 新增 `carrier_code: Mapped[str | None]`（default None）。
- 新增部分唯一索引 `uq_active_carrier`：`postgresql_where=text("carrier_code IS NOT NULL")` 上 unique——保证同时只有一个活跃 SerialUnit 持有某载体码；解绑置空即释放，天然可复用。
- `status` 取值新增 `"pending"`（预生成后、首站投产前）。既有 `in_process`/`finished`/`scrapped`/`reworking` 不变。pending 单元 `current_operation_seq=0`、`carrier_code=None`。

**新表 `carrier_binding`**（绑定/解绑历史，追溯用）：
- `id: int` PK
- `serial_unit_id: int` FK → serial_units.id
- `carrier_code: str`
- `bound_at: datetime`（server_default now）
- `unbound_at: datetime | None`（解绑时填写；活跃绑定=NULL）
- `operator_id: int | None` FK → users.id
- + TimestampMixin

**WorkOrder**：不变。`qty`=计划量（也是预生成 SN 数量）；`produced_qty` 仍由完工事件递增。

**迁移**：一条 Alembic 迁移——SerialUnit 加 carrier_code 列 + 部分唯一索引 uq_active_carrier + 建 carrier_binding 表（含 FK）。确认不误删既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component/uq_operator_skill_user_skill）。

---

## 4. 服务层

**`release_work_order` 扩展**（`production/service.py`）：
- 前置校验：`qty > 0`；工单 `sn_rule_id` 非空（无 SN 规则 → ValueError，因为要预生成）。
- 状态 created → released。
- 批量预建：循环 qty 次，用现有 `SnGenerator.next_sn(rule)` 取号（沿用其行锁逻辑，不改 SnGenerator），每次 `SerialUnit(sn=..., work_order_id, product_id, status="pending", current_operation_seq=0)` add + flush。
- 幂等/防重：仅 created→released 一次，已 released 不重复预建（现有守卫已保证）。

**新 `CarrierService`**（`production/carrier_service.py`）：
- `bind_and_pass_first(work_order_id, carrier_code, work_station_id, operator_id) -> OperationPassResult`：
  1. 取该工单下第一个 `status="pending"` 的 SerialUnit（按 id 升序）；无 → `BusinessRuleError("工单 SN 已全部投产，请选择新工单")`。
  2. 检查 carrier_code 是否已被活跃 SerialUnit 绑定（`carrier_code=:c AND status NOT IN ('finished','scrapped')`）；有 → `BusinessRuleError("载体码已绑定其他产品，请先解绑")`。
  3. 写 `su.carrier_code = carrier_code`；insert `carrier_binding(serial_unit_id, carrier_code, operator_id)`。
  4. 调用 `pass_operation(OperationPassInput(work_station_id, sn=su.sn, operator_id, components, params))` 完成首工序（pass_operation 内 status pending→in_process 需在 §4 pass 侧支持，见下）。
- `unbind(scan, operator_id) -> SerialUnit`：按 SN 或 carrier_code 找活跃 SerialUnit；无 → `NotFoundError`。清 `su.carrier_code=None`；把该 SN 最新未解绑的 carrier_binding 行 `unbound_at=now`。返回 su。

**`pass_operation` 定位逻辑扩展**（`operation_pass_service.py`）：
- 现状：`data.sn` 优先，否则 `work_order_code` 首件生成 SN。
- 新增：SN 查不到时，按 carrier_code 查活跃 SerialUnit（`carrier_code=:scan AND status NOT IN ('finished','scrapped')`）。命中后与 SN 命中走同一后续流程。
- pending 单元过站：pending SerialUnit 视同待过首工序（current_operation_seq=0），过站时 status pending→in_process（在既有"工单/返工件状态复位"段旁加 `if su.status == "pending": su.status = "in_process"`）。
- 首件 work_order_code 分支调整：因预生成后已有 pending 单元，该分支**不再新生成 SN**（否则会超 qty），改为"取该工单下第一个 pending SerialUnit 过首工序"（复用 CarrierService 的取号逻辑，但不绑载体码）。若无 pending → `BusinessRuleError("工单 SN 已全部投产")`。这样 API 直连 / 无载体码场景仍可用工单号首件过站，且不破坏 qty 上限。惰性 `SnGenerator.next_sn` 仅在预生成时调用。

**Schemas**：`CarrierBindInput(work_order_id, carrier_code, work_station_id, components[], params[])`（operator_id 服务端赋值）；`CarrierUnbindInput(scan)`。

---

## 5. 交互 / 路由

**工位作业首站流（`/production/station`）**：
1. 就绪页新增"**先选工单**"输入（扫/输入工单号 → POST /station/select-wo 只读校验，返回工单信息 + 剩余 pending 数量）。
2. 选定后锁定当前投产工单，扫码框语义变为**扫载体码**；显示"剩余待投产 N 件"。
3. 每扫一个载体码 → `POST /production/station/bind-and-pass`（CarrierService.bind_and_pass_first，operator_id=current_user）→ 成功刷新剩余数量 + 富界面（可继续绑物料/参数/过首工序）。
4. **用完**（无 pending）→ 绿色提示"✓ 工单 WO-XXX 已全部投产（N 件），请选择新工单" → 重置工单选择态。
5. 后续站（非首站）不变：`POST /production/station/load` + `/pass` 扫 SN 或载体码，自动识别。

**解绑入口**：`/trace/carrier-unbind`（简单页：扫/输入 SN 或载体码 → 解绑确认 → 结果片段）。放追溯管理模块。

**极简扫码页下线**：`home.html` 移除 `/production/scan` 导航卡片；路由代码暂保留（不入口），后续阶段可删。

**安全**：写操作 require_login；operator_id 服务端赋值（防伪造，沿用 P2d）；HTMX `{{ }}` 自动转义。

---

## 6. 范围边界（不做，留后期）

- SN 标签打印功能（模板/批量打印/重打/打印状态）——本期不建。
- 载体码类型区分（物料码 vs 托盘码分类）——本期通用不分。
- 工位-工单排产预绑（首站仍需手动选工单）。
- pending SN 的报废/取消（预生成后不投产的处理）——本期不涉及，pending 单元留存。
- PLC 采集、SOP 内容——沿用 P2d 占位。

---

## 7. 子任务切分（约 5 个，顺序，各自 TDD + 复审）

1. 数据模型：SerialUnit.carrier_code + uq_active_carrier 部分唯一索引 + carrier_binding 表 + 迁移。
2. release_work_order 批量预生成 SerialUnit（status=pending）+ qty/sn_rule 校验 + 测试（含幂等）。
3. pass_operation 载体码定位 + pending→in_process 状态转移 + 测试（SN 命中 / 载体码命中 / pending 过首工序）。
4. CarrierService（bind_and_pass_first 顺序取号+绑定+过首工序+用完拦截；unbind 解绑+历史）+ 测试（顺序赋值/用完/重复绑/解绑复用）。
5. 工位作业首站流（选工单+扫载体码 bind-and-pass+剩余数量+用完提示）+ 解绑页 + 首页移除 scan 入口 + 端到端测试。

---

## 8. 测试策略

- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：
  - release 批量预生成：qty 条 pending SerialUnit，SN 号连续；qty=0 或无 sn_rule → 拒绝；重复 release 不重复预建。
  - carrier_code 部分唯一索引：两个活跃 SerialUnit 绑同一 carrier_code → IntegrityError；解绑后该 carrier_code 可再绑新单元。
  - bind_and_pass_first：顺序取下一 pending（id 升序）；工单 pending 用完 → BusinessRuleError；载体码已绑 → BusinessRuleError；成功后 status in_process + carrier_binding 有活跃行。
  - unbind：清 carrier_code + carrier_binding.unbound_at 填写；解绑后复用；不存在 → NotFoundError。
  - pass_operation 载体码定位：扫载体码命中活跃单元并推进；扫 SN 仍正常；SN 与载体码指向同一单元结果一致；pending 单元首过 status 转 in_process。
  - work_order_code 首件分支：取第一个 pending 单元过首工序、不新生成 SN、不超 qty；pending 用完 → BusinessRuleError。
  - 首站流页面：选工单显示剩余数量；扫载体码投产刷新；用完提示新工单；require_login；operator_id 防伪造。
  - 后续站扫载体码/SN 自动识别（回归 P2d load/pass）。
  - 极简 /production/scan 路由仍可用（未删），但首页无入口。

---

## 9. 下一步

本 spec 覆盖 P2e（SN 生命周期重构 + 载体码过站）。经复核后走 writing-plans 出实现计划 → subagent-driven-development 执行 → 终审 → 合并。
