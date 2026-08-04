# P2a 设计文档：层级模型重构 + 追溯体系重建（地基）

- 日期：2026-08-07
- 状态：设计已获用户口头确认，待书面复核
- 上游：`2026-07-31-lightmes-design.md`（总体）、P1a/b/c 已合并（旧两层模型）
- 定位：车间执行能力跃升的**地基**。后续 P2b（主数据+ERP同步）、P2c（技能）、P2d（工位作业主界面）都建在本期之上。

---

## 1. 背景与目标

P1 MVP 用的是两层模型：`routing → routing_step → station`。现要升级为符合 MES 标准的**物理/工艺分离三层模型**，并借此把追溯体系一次性建对，为后续 ERP API 交互打好基础。

**层级（已确认，物理/工艺分离）：**
- **物理**：产线 line → 作业站 work_station（设备/场地布局，一条产线有多个有序作业站）
- **工艺**：工单 → 产品 → 工艺路径 routing → 工序 operation；每道工序**分配到**某作业站执行
- **工单绑产线**；同一产品的工艺路径可在多条产线上跑（物理/工艺解耦）
- **工序 operation 是追溯的最小单位**

**策略：推倒重建。** 现 DB 仅测试数据，无生产数据。旧 station/routing_step/station_pass/serial_unit/genealogy 相关表 drop 重建；P1 经终审锤炼的**逻辑思路全部复用**（前向防重复、乐观锁、绑料原子性、防重复计数、正反向召回、返工），只是重新落到新三层模型、追溯挂到工序记录上。

---

## 2. 范围

### P2a 做
- masterdata：`line`/`work_station` 模型；`routing` 保留、`routing_step`→`operation` 重构；facade 扩展
- production：`work_order` 加 `line_id`；`serial_unit`（`current_operation_seq`）；`operation_record`（追溯最小单位）；`operation_param`（参数快照，手动录入 + 预留自动）；`OperationPassService`（三层校验链）
- trace：`genealogy_bind` 挂 `operation_record`；`TraceService`（履历/正向/反向/工艺参数追溯）；`ReworkService` 重落
- 迁移：推倒重建（drop 旧表建新表，演示数据丢弃）
- seed 脚本重写（含产线/作业站/工序的示范线）
- 全套真实 PostgreSQL 集成测试

### P2a 明确不做（留后期，仅留字段/钩子）
- 主数据管理 UI、ERP `source`/同步抽象 → **P2b**
- 技能校验逻辑 → **P2c**（operation 上留 `required_skill_id`/`required_level` 字段 + service 钩子，默认放行）
- 工位作业主界面、SOP 内容、可插拔面板 → **P2d**（operation 上留 `sop_id`/`panels` 字段）
- 自动采集（MQTT→参数）→ **P3**（operation_param 留 `source=auto` 入口）
- 旧扫码/追溯/返工**页面**：P2a 只保证 API + service 跑通；旧页面因模型变化失效，P2a 用最小改动让其不报错或临时下线，正式富界面 P2d 重做

---

## 3. 数据模型

所有表默认含 `id`、`created_at`、`updated_at`（继承 `TimestampMixin`）。

### 3.1 物理层（masterdata）
**`line`（产线）表 `lines`**
- `code`（唯一,索引）、`name`、`description`(可空)、`is_active`(默认 True)

**`work_station`（作业站）表 `work_stations`**
- `code`（唯一,索引）、`name`、`line_id`(FK→lines)、`seq`(int,产线内顺序)、`description`(可空)、`is_active`(默认 True)
- 唯一约束 `(line_id, seq)`

### 3.2 工艺层（masterdata）
**`routing`（工艺路径）** —— 保留：`code`(唯一)、`name`、`product_id`(FK)、`version`、`status`；沿用同产品单一 active 部分唯一索引。

**`operation`（工序，替代 routing_step）表 `operations`**
- `routing_id`(FK→routings)、`seq`(int,工艺顺序,追溯排序依据)、`code`、`name`
- `default_work_station_id`(FK→work_stations,该工序默认作业站)
- `is_mandatory`(默认 True)
- `required_skill_id`(FK,可空,**P2c 用**)、`required_level`(int,可空,**P2c 用**)
- `sop_id`(可空,**P2d 用**)、`panels`(JSON,可空,可插拔面板配置,**P2d 用**)
- 唯一约束 `(routing_id, seq)`

**`bom`/`bom_item`** —— 保留不动（谱系依赖）。

### 3.3 生产/追溯层（production + trace）
**`work_order`** —— 加 `line_id`(FK→lines,工单绑产线)；其余(product/routing/qty/status/sn_rule_id/produced_qty)保留。

**`serial_unit`** —— 保留 sn/work_order_id/product_id/status/version；`current_step_seq` → `current_operation_seq`(int,指向 operation.seq)；`is_counted`(bool,防重复计数,沿用 P1c)。

**`operation_record`（工序记录，替代 station_pass，追溯最小单位）表 `operation_records`**
- `serial_unit_id`(FK)、`work_order_id`(FK)、`operation_id`(FK→operations)、`work_station_id`(FK→work_stations)、`line_id`(FK→lines)
- `operator_id`(FK→users,可空)、`start_time`(可空)、`end_time`(可空,server_default now)、`result`(pass/fail,默认 pass)、`remark`(可空)

**`operation_param`（工序参数快照）表 `operation_params`**
- `operation_record_id`(FK→operation_records)、`param_key`、`param_value`、`unit`(可空)、`source`(manual/auto,默认 manual)、`recorded_at`(server_default now)
- MVP 手动录入进此表；**P3 采集数据同表 source=auto**

**`genealogy_bind`** —— `station_pass_id` → `operation_record_id`(FK→operation_records)；其余(parent_sn_id/component_type/component_sn/component_batch_no/qty/status/unbind_*/唯一件 active 部分唯一索引)不变。

### 3.4 关系图（文字）
```
line 1─* work_station              (物理:产线含有序作业站)
product 1─* routing 1─* operation *─1 work_station(default)   (工艺:工序分配到作业站)
work_order *─1 line, *─1 product, *─1 routing
work_order 1─* serial_unit 1─* operation_record *─1 operation
operation_record *─1 work_station, *─1 line
operation_record 1─* operation_param        (工艺参数追溯)
operation_record 1─* genealogy_bind          (物料谱系挂工序记录)
```

---

## 4. 核心流程（复用 P1 经终审锤炼的逻辑，升级为三层校验）

### 4.1 过站校验链（`OperationPassService`，替代 P1 StationPassService）
输入：`work_order_code`(首件) 或 `sn`、`work_station_id`(当前扫码作业站)、`operator_id`、可选 `components`、可选 `params`。单事务：
1. 定位工单/SN；工单状态须 `released`/`in_process`，否则 `BusinessRuleError`。SN 为 finished/scrapped 拒绝（reworking 放行，沿用 P1c）。
2. 首件生成 SN（沿用可配置 SN 生成器，行锁+populate_existing，不动）。
3. **期望下一工序** = routing 中 `seq > current_operation_seq` 的第一个 operation（前向唯一→天然防重复，沿用 P1c 修正，不再用 exists 守卫）。无后续→`BusinessRuleError` 已完工。
4. **三层防跳站**：当前 `work_station_id` 必须 = 期望工序的 `default_work_station_id`；且该作业站 `line_id` = 工单 `line_id`。否则 `BusinessRuleError`（提示应到的产线/作业站/工序）。
5. **技能校验钩子（P2c 填）**：若工序配 `required_skill_id`，校验 operator 的 user_skill 达标；P2a 留接口默认放行。
6. 写 `operation_record(result=pass, line_id, work_station_id)`；乐观锁 guarded UPDATE 更新 `serial_unit.current_operation_seq/version`，rowcount==0→`ConflictError`（沿用 P1c）。
7. **绑料**（同事务，沿用 P1c `GenealogyService`）：绑到本次 operation_record；BOM 类型校验 + 唯一件占用反查 + 批次；任一失败 `db.rollback()` 整单回滚（沿用原子性）。
8. **参数录入**：若带 params，逐条写 `operation_param(source=manual, operation_record_id=本次)`。
9. **末工序完工**：is_last(max seq)→`serial_unit.status=finished`；`is_counted` 门控原子自增 `produced_qty`（沿用 P1c 防重复计数修复）；达 qty→工单 `completed`；发 `SerialUnitFinished`。
10. 工单 released→in_process；serial reworking→in_process 复位（沿用 P1c）。
11. 发 `OperationPassed` 事件。

### 4.2 追溯查询（`TraceService` 重落）
- **产品履历**：SN → operation_record 时间线（工序级），每条含其 operation_param 快照 + 绑定的 genealogy_bind。
- **物料正向**：成品 SN → 全部组件（active，可选含历史）。
- **物料反向（召回）**：组件 SN/批次 → 装入的成品（含已拆解历史，标状态）。沿用 P1c history-inclusive 语义。
- **工艺参数追溯（新增）**：SN → 各工序采集参数（跨 operation_record 汇集 operation_param）。

### 4.3 返工（`ReworkService` 重落）
回退到指定 operation seq + 可选解绑 genealogy_bind id 列表 + 乐观锁 guarded UPDATE；reworking 件重新过站沿用放开逻辑；scrap 终态仅 in_process/reworking 可判废。沿用 P1c。

---

## 5. 跨模块约定（沿用既有）
- production/trace 读 masterdata **只走 `MasterDataQueryService`**（扩展：`get_line`/`get_work_station`/`get_operations(routing_id)`(按 seq)/`get_active_bom_items`）。
- production→trace 调 `GenealogyService` 公开接口；trace→production 只读用其 repository（P1c 既定的 MVP 读/命令耦合，沿用）。
- 领域异常体系（`NotFoundError`/`ConflictError`/`ValidationError`/`BusinessRuleError` + 全局 handler）沿用。
- 事件总线沿用：`OperationPassed`/`SerialUnitFinished`/`GenealogyBound`/`GenealogyUnbound`/`SerialUnitReworkStarted`；trace 订阅 `OperationPassed` 为 no-op/日志 handler。
- get_db 请求级事务边界；repository 只 flush；乐观锁 guarded UPDATE；真实 PostgreSQL 集成测试；HTMX 写处理器吞异常前 rollback（旧页面若保留）。

---

## 6. 子任务切分（P2a 内部，顺序，各自 TDD + 复审）
1. masterdata：`line` + `work_station` 模型 + 迁移 + facade + repository
2. masterdata：`routing_step`→`operation` 重构（drop 旧 routing_steps/stations，建 operations，含 default_work_station_id + 预留字段）+ facade get_operations
3. production：`work_order` 加 line_id + `serial_unit`(current_operation_seq/is_counted) 模型/迁移（drop 旧）
4. production：`operation_record` + `operation_param` 模型/迁移 + repository
5. trace：`genealogy_bind` 重挂 operation_record + `GenealogyService` 重落（+测试）
6. production：`OperationPassService` 三层校验链 + 手动参数录入（复用 P1 逻辑，含 SN 并发测试）（+测试）
7. trace：`TraceService`（履历/正反查/参数追溯）+ `ReworkService` 重落（+测试，含多步返工、召回、参数追溯）
8. seed 脚本重写（产线+作业站+工序示范线）+ 全量回归

> 迁移策略：因推倒重建，任务 2/3 会 drop 旧表。实现时用 alembic 迁移显式 drop 旧表再建新表；因无生产数据，不需数据搬迁。每次 autogenerate 后确认只动预期表（沿用元数据对齐纪律，避免部分索引漂移）。

---

## 7. 错误处理与并发
- 校验失败抛领域异常（多为 BusinessRuleError 422），API 走全局 handler。
- 过站+绑料+参数单事务：任一失败整单回滚（无残留 SN/记录，沿用 P1c 原子性验证）。
- 并发双扫：serial_unit 乐观锁；SN 流水行锁；唯一件占用 DB 部分唯一索引兜底。

---

## 8. 测试策略
- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点覆盖：**三层防跳站**（作业站属产线、工序默认作业站匹配）、参数快照进追溯、履历工序级、正/反向召回（含已拆解）、工艺参数追溯、返工多步重过、防重复计数、绑料原子回滚、SN 并发唯一。
- 把 P1 验证过的场景在新模型重落再验一遍（回归等价性）。

---

## 9. 待办 / 开放问题（不阻塞 P2a）
- 同一工序可在多个作业站做（MVP 用 default_work_station_id 单站；多站分配留后）。
- operation_param 的参数字典/校验（上下限）——MVP 自由 key-value，规范化留后。
- 旧页面临时处理方式（下线 vs 最小改）在实现时按最省事的定；正式界面 P2d 重做。
- 金蝶版本未定——P2b 才涉及，P2a 不碰。

---

## 10. 下一步
本 spec 覆盖 P2a（层级重构 + 追溯重建）。经复核后走 writing-plans 出实现计划。P2a 完成后依次推进 P2b（主数据+ERP抽象）、P2c（技能）、P2d（工位作业主界面）。
