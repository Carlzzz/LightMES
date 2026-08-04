# P1c 设计文档：追溯 + 物料谱系 + 返工（P1 收官段）

- 日期：2026-08-04
- 状态：设计待用户书面复核
- 上游：`2026-07-31-p1-mvp-design.md`（P1 MVP，§4.3 谱系模型、§5.3 绑定、§5.4 返工、§5.5 追溯）
- 前置：P0 + P1a + P1b 已合并到 master（过站主线、乐观锁、领域异常、masterdata 只读 facade、事件总线骨架）

---

## 1. 目标与范围

P1 的收官段。在 P1b 过站主线上补齐**物料谱系绑定、正/反向追溯、产品履历、返工/拆解**，形成完整追溯闭环：装配时把消耗的组件绑定到成品 SN，成品出问题能查它由哪些批次/唯一件构成，某批次组件出问题能反查装进了哪些成品（召回）；不良品能返工回退重做，全程历史保留。

现场目标：扫码工位在过站时一并扫组件完成绑定；追溯查询页支持正/反查与履历；返工操作可回退 SN 重做。

### 做
- `genealogy_bind` 模型 + 迁移（trace 模块）
- masterdata facade 扩展：`get_active_bom` / active BOM items（还 P1b 记的账）
- `GenealogyService`：绑定（自由绑定 + BOM 类型校验 + 唯一件占用反查）、解绑
- 过站集成绑定：`StationPassInput` 加 `components`，过站事务内同步绑定（用户选定"过站时一起扫组件"）
- `TraceService`：产品履历、正向查（成品→组件）、反向查（组件→成品），单层
- `ReworkService`：返工（回退 seq + 可选解绑）、判废
- pass_station 放开 `reworking` 状态 SN 过站并复位 in_process
- **事件接入**（还 P1b 记的账）：发布 StationPassed/GenealogyBound/GenealogyUnbound/SerialUnitReworkStarted/SerialUnitFinished；trace 订阅 StationPassed 为空/日志级 handler（证明总线通）
- 扫码页加组件输入、追溯查询页、返工操作页（HTMX，写处理器遵守 rollback 约定）

### 明确不做（YAGNI，留后期）
- 多层谱系递归（半成品谱系树展开）→ 留后；MVP 单层
- 完整批次管理（库存/收发存/FIFO/有效期）→ P4；批次号仅绑定时录入标识
- 工序级强制绑定项（binding_config 预留字段仍不启用）
- 独立返工工艺路线（返工走原路线）
- 跨模块异步事件消费（事件仅"已发生"通知，绑定在事务内同步）
- 不回改 P1a 旧的裸 ValueError

---

## 2. 关键实现决策（已定）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 绑定接入 | 过站时一起扫组件；`StationPassInput.components` 可选列表；过站事务内同步绑定，任一失败整体回滚 |
| 2 | active BOM 读取 | facade 加 `get_active_bom(product_id)` + active BOM items；绑定校验走 facade |
| 3 | 事件 | 过站/绑定/解绑/返工/完工发布事件；绑定仍同步在事务内；trace 订阅 StationPassed 为 no-op/日志 handler |
| 4 | 追溯深度 | 单层（成品→直接组件 / 组件→直接父）；多层递归留后 |
| 5 | 返工 | 回退到指定 seq + 可选解绑 bind id 列表，走原路线；reworking→in_process 由首次重新过站复位；scrap 终态 |
| 6 | rollback 约定 | P1c 的 HTMX 写处理器吞 DomainError 前先 `db.rollback()` |
| 7 | 唯一件占用 | 绑定 serial 组件校验其 component_sn 未被另一 active 成品占用（反查防重复装配） |

---

## 3. 架构落位

新增 `trace` 模块（`models/schemas/repository/service/router/__init__.py` + `register(app)`），沿用 P0/P1a/P1b 全部约定：模块只暴露 service、跨模块读走 facade、领域异常、真实 DB 测试、require_login 写守卫、HTMX 自动转义、get_db 事务边界。

模块协作：
- `production` 的 `StationPassService` 在过站事务内调用 `trace` 的 `GenealogyService.bind_components(...)` 完成绑定。这是 production→trace 的同步调用（调对方 service 公开接口，允许）。
- `trace` 绑定校验需读 active BOM → 走 `MasterDataQueryService`（facade），不碰 masterdata repository。
- 事件：production 发布 StationPassed 等；trace 的 register 订阅 StationPassed（no-op/日志 handler）。

> production 依赖 trace（过站调绑定），trace 依赖 masterdata facade。无环：masterdata←production←(调用)→trace←masterdata facade。production→trace 是单向调用，trace 不反向调 production。

---

## 4. 数据模型（trace 模块新增）

沿用风格：`Mapped[]`/`mapped_column()`，继承 `Base`+`TimestampMixin`。

**genealogy_bind（物料谱系绑定）表 `genealogy_binds`**
- `id:int PK`
- `parent_sn_id:int FK serial_units.id`（成品单元）
- `component_product_id:int FK products.id`（组件物料）
- `component_type:str`（`serial`/`batch`）
- `component_sn:str|None`（唯一件时填，索引）
- `component_batch_no:str|None`（批次件时填，索引）
- `qty:Numeric(12,3) default 1`
- `bind_time:datetime server_default now()`（tz-aware，沿用 P1b 修正）
- `operator_id:int|None FK users.id`
- `station_pass_id:int|None FK station_passes.id`（在哪次过站绑定）
- `status:str default "active"`（`active`/`unbound`）
- `unbind_time:datetime|None`、`unbind_reason:str|None`
- 索引：`parent_sn_id`（正向查）、`component_sn`、`component_batch_no`（反向查）
- 迁移：新增 `genealogy_binds` 表，FK 指向 serial_units/products/station_passes/users。

---

## 5. 核心机制

### 5.1 组件绑定（GenealogyService，自由绑定 + 类型校验）
`GenealogyService(db)`，方法 `bind_components(parent_su, components, operator_id, station_pass_id) -> list[GenealogyBind]`。对每个组件项（`component_product_id` + `component_sn` 或 `component_batch_no` + 可选 qty）：
1. 读成品 active BOM（facade `get_active_bom(parent_su.product_id)`）；无 active BOM → `BusinessRuleError`。
2. 校验 `component_product_id` 在 BOM items 内；否则 `BusinessRuleError("组件不属于本产品 BOM")`。取该 BOM item 的 `track_mode`（P1a 已冗余）。
3. `track_mode=serial`：必须有 `component_sn`（否则 `ValidationError`）；反查该 sn 是否已被另一 active genealogy_bind 占用（parent 为 in_process/finished 的成品）→ 若占用 `ConflictError("该唯一件已装配在其他成品上")`。
4. `track_mode=batch`：必须有 `component_batch_no`（非空，否则 `ValidationError`）。
5. 写 `genealogy_bind(status=active, station_pass_id=...)`；发布 `GenealogyBound` 事件。
- 由过站服务在同一事务内调用；任一组件失败抛异常 → 整个过站事务回滚。

### 5.2 过站集成绑定（扩展 StationPassService）
- `StationPassInput` 加 `components: list[ComponentInput] = []`（`ComponentInput`: `component_product_id:int`, `component_sn:str|None`, `component_batch_no:str|None`, `qty:float=1`）。
- pass_station 在**步骤 7（写 station_pass + 乐观锁更新）之后、步骤 8（末站完工）之前**插入：若 `data.components` 非空，调 `GenealogyService.bind_components(su, data.components, operator_id, 本次 station_pass.id)`。需要 station_pass 的 id → 步骤 7 的 `passes.add(...)` 返回对象供绑定引用。
- **放开 reworking**：步骤 1 的"finished/scrapped 拒绝"保持（reworking 不在拒绝之列，故 reworking 的 SN 可过站）；过站成功后若 `su.status == "reworking"` 复位为 `in_process`（与"released→in_process"并列处理）。
- 过站成功发布 `StationPassed`；末站完工发 `SerialUnitFinished`。

### 5.3 返工 / 拆解（ReworkService）
`ReworkService(db)`：
- `rework(sn, target_seq, unbind_bind_ids=[], reason=None, operator_id=None) -> SerialUnit`：
  1. 取 SN；不存在 → `NotFoundError`；`scrapped` → `BusinessRuleError`。
  2. `target_seq` 必须 `< current_step_seq` 且 ≥ 0，否则 `ValidationError`。
  3. 对 `unbind_bind_ids` 中每个 genealogy_bind：校验属于本 SN 且为 active，置 `status=unbound`、`unbind_time=now`、`unbind_reason=reason`；发 `GenealogyUnbound`。
  4. 乐观锁更新 serial_unit：`status=reworking`、`current_step_seq=target_seq`、`version+1`（冲突 → `ConflictError`）。
  5. 发 `SerialUnitReworkStarted`。
- `scrap(sn, reason=None) -> SerialUnit`：置 `status=scrapped`（终态）。MVP 仅允许 `in_process`/`reworking` 判废；`finished`/`scrapped` 判废 → `BusinessRuleError`（finished 拆解/召回判废留后）。
- 返工后该 SN 由扫码工位从 `target_seq` 之后重新过站；首次重新过站时 pass_station 把 `reworking` 复位 `in_process`（见 5.2）。防重复天然满足（只看 `current_step_seq` 之后）。

### 5.4 追溯查询（TraceService，单层）
`TraceService(db)`：
- `genealogy_of(sn) -> ProductGenealogy`：给成品 SN → 其全部 genealogy_bind（默认 active；参数 `include_unbound` 可含历史，标状态）。正向，单层。
- `where_used(component_sn=None, component_batch_no=None) -> list[ParentRef]`：给组件 SN 或批次号 → 装入的成品 SN 列表（含已拆解历史，标 active/unbound）。反向，单层，召回关键。
- `history_of(sn) -> ProductHistory`：产品履历 = station_pass 时间线（复用 `StationPassRepository.list_by_serial_unit`）+ genealogy_bind（active+历史）。

---

## 6. 事件（接入，还 P1b 账）
在 production/trace 的服务里真正 publish（用 P0 的 `event_bus`）：
- `StationPassed`（过站成功）、`SerialUnitFinished`（末站完工）、`SerialUnitReworkStarted`（返工）、`GenealogyBound`（绑定）、`GenealogyUnbound`（解绑）。
- 事件为 dataclass，带关键 id（sn/serial_unit_id/work_order_id 等）。
- trace 的 `register(app)` 订阅 `StationPassed` → 一个 no-op/日志级 handler（证明总线通、为 P4/AI 预留）。**绑定不靠事件**——仍在过站事务内同步完成，保证原子性。
- 说明：事件在事务提交语义上"尽力"——MVP 在 service 内 publish（同步分发），若 handler 无副作用则无一致性风险；将来若加有副作用订阅方再引入 outbox 等。

---

## 7. 前端（HTMX）
- **扫码工位页**（扩展 P1b 的 scan.html）：过站表单增加"组件"区——可添加多行组件输入（product 选择/扫码 + SN 或批次号）。提交时随过站一起发。结果片段显示过站结果 + 已绑定组件数。
- **追溯查询页** `/trace/query`：输入成品 SN → 展示履历（过站时间线）+ 正向谱系（组件列表，标 active/unbound）；输入组件 SN/批次 → 反向展示装入的成品。
- **返工操作页** `/trace/rework` 或过站页入口：输入 SN + 目标工序 + 勾选要解绑的组件 → 提交返工；写处理器吞异常前 rollback。
- HTMX 自动转义；本地托管 htmx。写操作 require_login。

---

## 8. 子任务切分建议（单一计划，多任务）
P1c 作为一个实现计划，内部任务顺序：
1. facade 扩展 `get_active_bom` + active BOM items（+测试）
2. 事件定义（dataclass 事件类）+ production 过站发布 StationPassed/SerialUnitFinished + trace no-op 订阅（+测试证明总线通）
3. genealogy_bind 模型 + 迁移 + repository（trace 模块骨架）
4. GenealogyService 绑定/解绑（自由绑定+类型校验+唯一件占用反查）（+测试）
5. 过站集成绑定（StationPassInput.components + pass_station 调 bind，同事务；放开 reworking）（+测试）
6. TraceService 履历/正查/反查（单层）（+测试）
7. ReworkService 返工/判废（+测试）
8. HTMX：扫码页加组件、追溯查询页、返工页（+页面测试）
（实现时可再细分；每任务独立可测、TDD、频繁提交。）

---

## 9. 错误处理与并发
- 绑定/返工校验失败抛领域异常（多为 BusinessRuleError 422 / ConflictError 409 / ValidationError 400），API 走全局 handler，HTMX 页面吞异常前 `db.rollback()` 再渲染红片段。
- 过站+绑定单事务：绑定失败回滚整个过站（含 SN 生成）。
- 返工用 serial_unit 乐观锁（沿用 P1b）。
- 唯一件占用反查防重复装配。

---

## 10. 测试策略
- 真实 PostgreSQL 集成测试（db_session 回滚 fixture），TDD。
- 重点覆盖：facade active BOM 读取；绑定（BOM 类型校验/唯一件占用拒绝/批次；serial 缺 sn 拒绝）；过站+绑定原子性（绑定失败整个过站回滚、无残留 SN/pass）；reworking 放开过站并复位 in_process；正向查/反向查（含 unbound 历史标注）；履历；返工（回退 seq + 解绑 + 乐观锁）；判废终态；事件发布（订阅 handler 被调用证明总线通）。
- HTMX 页面测试（TestClient + require_login + rollback 验证无残留）。

---

## 11. 待办 / 开放问题（不阻塞实现）
- 多层谱系递归展开——留后；MVP 单层。
- 事件的事务一致性（outbox 模式）——MVP 无副作用订阅方，暂不需要；有副作用订阅方出现时再引入。
- 扫码页组件行的前端交互（动态加行）——实现时用最简 HTMX（如"添加一行"按钮 append 输入行）敲定。
- finished 成品是否允许召回性判废/拆解——MVP 仅 in_process/reworking 可 scrap；finished 拆解留后。

---

## 12. 下一步
本 spec 覆盖 P1c（追溯 + 谱系 + 返工），P1 收官。经复核后走 writing-plans 出实现计划。P1c 完成即 MVP（P1）全部落地：配线→工单→过站→绑定→追溯→返工闭环。

