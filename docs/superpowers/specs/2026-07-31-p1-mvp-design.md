# P1 (MVP) 设计文档：过站追溯主线 + 物料谱系

- 日期：2026-07-31
- 状态：设计待用户书面复核
- 上游：`2026-07-31-lightmes-design.md`（总体设计，§5 分期路线、§6 MVP 边界、§7 数据模型草案）
- 前置：P0 工程地基已合并到 master（模块化单体骨架、auth 竖切、`register(app)` 约定、`get_db` 工作单元、事件总线、真实 DB 测试、Alembic）

---

## 1. 目标与范围

在 P0 骨架上实现 MVP 主线：**能配置一条产线 → 建工单 → 生成 SN → 沿工艺路线过站（防跳站/防重复）→ 绑定消耗的组件 → 完整追溯（履历 + 物料谱系正反查）→ 返工/拆解**。手工/扫码驱动，不接 ERP、不接设备采集。

现场目标：一个能用的网页扫码工位，让装配线当天就能用扫码防错 + 追溯。

### 明确不做（YAGNI，留给后续期）
- 不接金蝶 ERP（P2）、不接设备采集（P3）
- 不做 SPC / 来料/过程检验 / 不良管理（P4）
- 不做 OEE / 点检（P5）
- 不做排产算法（工单手工排期字段）
- 不做完整批次管理（批次库存/收发存/FIFO/有效期 → P4）；批次号仅为绑定时录入的标识
- 不做按工序强制绑定项（强防错留后）；MVP 仅"组件类型属于 BOM"校验
- 不做独立返工工艺路线；返工回退到原路线的指定工序
- 主数据不做导入导出/审批/版本对比

---

## 2. 关键实现决策（已定）

以 MES 产品专家视角拍定，均在既定 MVP 边界内：

| # | 主题 | 决策 |
|---|---|---|
| 1 | 过站交互 | 单页扫码工位（HTMX 服务端渲染）；API 为 API-first 副产物 |
| 2 | 组件绑定 | 自由绑定 + BOM 类型校验；routing_step 预留强制绑定配置槽位（不实现） |
| 3 | SN 规则语法 | 占位符模板：`{PREFIX}` `{YYYY}/{YY}/{MM}/{DD}` `{SEQ:n}`；`seq_reset`=never/daily/monthly |
| 4 | SN 生成时机 | 首站过站时按需生成（非工单创建即批量生成） |
| 5 | 工单状态 | `created→released→in_progress→completed→closed`；手工创建/release，首个 SN 过站自动转 in_progress |
| 6 | WIP 表达 | 无独立 WIP 表；用 `serial_unit.status + current_step_seq + current_station_id` |
| 7 | 返工范围 | 回退到指定工序序号 + 可选解绑组件；走原路线；scrap 为终态 |
| 8 | 主数据界面 | 最简 CRUD 页（列表/新增/编辑，HTMX） |

---

## 3. 架构落位（沿用 P0 约定）

新增业务模块，全部遵循 P0 已立的约定：
- 模块目录 `src/lightmes/modules/<name>/`，含 `models.py / schemas.py / repository.py / service.py / router.py / __init__.py`
- 每模块 `__init__.py` 暴露 `register(app)`：`app.include_router(router)` + 订阅事件；`main.py` 调 `xxx.register(app)`
- 对外只暴露 service 层；跨模块协作走事件总线或调对方 service
- 事务边界在 `get_db`（请求级提交/回滚）
- API 端点用 `response_model` 类型化（喂 OpenAPI）
- 真实 PostgreSQL 集成测试（`db_session` 事务回滚 fixture）
- Alembic 迁移；前端 HTMX + Jinja2，第三方 JS 本地托管

P1 涉及的模块：
- `masterdata`（产品/工位/工艺路线/BOM）—— 或按内聚度拆分，见 §7
- `production`（工单、SN、SN 规则、过站、WIP）
- `trace`（谱系绑定、追溯查询、履历、返工）

模块间通过事件解耦，例如 `production` 发 `StationPassed`，`trace` 订阅。具体事件见 §8。

---

## 4. 数据模型

所有表默认含 `id`（PK）、`created_at`、`updated_at`（继承 P0 `TimestampMixin`）。命名用英文表名 + 中文说明。枚举用字符串列（配 CHECK 或应用层校验）。

### 4.1 主数据（masterdata 模块）

**product（产品/物料主数据）**
- `code`（唯一，索引）、`name`、`type`（`finished`/`semi`/`component`/`consumable`）
- `spec`、`unit`、`track_mode`（`serial`/`batch`/`none`）
- 说明：既表示成品也表示组件；`track_mode` 决定其作为组件时按 SN 还是批次绑定。

**station（工位）**
- `code`（唯一）、`name`、`description`、`location`（可空）、`is_active`

**routing（工艺路线，头）**
- `code`（唯一）、`name`、`product_id`（FK product）、`version`、`status`（`active`/`inactive`）
- 约束：同一 product 同时只允许一条 `active` 路线（应用层校验）。

**routing_step（工序 / 路线步骤）**
- `routing_id`（FK）、`seq`（顺序号，正整数）、`station_id`（FK station）、`name`
- `is_mandatory`（默认 True）
- `binding_config`（JSON，可空，**预留**——未来放"本工序强制绑定项"；MVP 不读不写逻辑）
- 唯一约束：`(routing_id, seq)`。

**bom（物料清单，头）**
- `product_id`（FK，成品）、`version`、`status`（`active`/`inactive`）
- 约束：同一 product 同时只一条 active BOM。

**bom_item（BOM 行）**
- `bom_id`（FK）、`component_product_id`（FK product，组件）、`qty`（数量，Numeric）
- `track_mode`（冗余自组件 product：`serial`/`batch`，绑定校验用）
- 唯一约束：`(bom_id, component_product_id)`。

### 4.2 生产与在制（production 模块）

**sn_rule（SN 编码规则，可配置）**
- `code`（唯一）、`name`、`product_id`（FK，可空 = 通用规则）
- `pattern`（模板串，如 `SN{YY}{MM}{DD}{SEQ:5}`）
- `seq_reset`（`never`/`daily`/`monthly`）、`current_seq`（整数，当前流水）、`seq_period_key`（字符串，记录当前流水所属周期，如 `2026-07`；周期变化时 `current_seq` 归零）
- 说明：生成器读取本表，占位符见 §5.1。生成流水时对本行加行锁保证并发唯一。

**work_order（工单）**
- `code`（唯一）、`product_id`（FK）、`routing_id`（FK）、`sn_rule_id`（FK，可空 = 用产品通用规则）、`qty`（计划数量）
- `status`（`created`/`released`/`in_progress`/`completed`/`closed`）
- `source`（`manual`；`erp` 预留）、`planned_start`、`planned_end`（可空，手工）
- `produced_qty`（已完工数量，冗余计数，随 SN 完工累加）

**serial_unit（产品单元 / SN）—— 同时承载 WIP 状态**
- `sn`（唯一，索引）、`work_order_id`（FK）、`product_id`（FK）
- `status`（`in_process`/`finished`/`scrapped`/`reworking`）
- `current_step_seq`（当前所在工序 seq，整数）、`current_station_id`（FK station，可空）
- `version`（乐观锁整数，防双扫并发）
- 说明：SN 在首站过站时创建，初始 `status=in_process`。WIP = 对本表按 work_order / station / status 过滤聚合。

**station_pass（过站记录 / 履历）**
- `serial_unit_id`（FK）、`work_order_id`（FK）、`routing_step_id`（FK）、`station_id`（FK）
- `operator_id`（FK auth.user，可空）、`pass_time`、`result`（`pass`/`fail`）、`remark`（可空）
- 说明：某 SN 的履历 = 其全部 station_pass 按 pass_time 排序。返工产生的重复过站也保留（多条同 step）。

### 4.3 追溯与谱系（trace 模块）

**genealogy_bind（物料谱系绑定）**
- `parent_sn_id`（FK serial_unit，成品）、`component_product_id`（FK product，组件）
- `component_type`（`serial`/`batch`）
- `component_sn`（可空，唯一件时填）、`component_batch_no`（可空，批次件时填）、`qty`（Numeric）
- `bind_time`、`operator_id`（可空）、`station_pass_id`（FK，在哪次过站绑定的，可空）
- `status`（`active`/`unbound`）、`unbind_time`（可空）、`unbind_reason`（可空）
- 索引：`parent_sn_id`（正向查）、`component_sn`、`component_batch_no`（反向查）
- 校验（应用层）：唯一件 `component_sn` 全局不得被两个 active 成品同时占用（反查防重复装配）。
- 可逆：拆解/返工时置 `status=unbound` 保留历史，绝不物理删除。

### 4.4 关系图（文字）
```
product 1─* routing 1─* routing_step *─1 station
product 1─* bom 1─* bom_item *─1 product(component)
product 1─* sn_rule
work_order *─1 product, *─1 routing, *─1 sn_rule
work_order 1─* serial_unit 1─* station_pass *─1 routing_step
serial_unit 1─* genealogy_bind  (parent_sn_id)
genealogy_bind *─1 product(component)
```

---

## 5. 核心机制

### 5.1 SN 生成器（可配置）
- 输入：一个 `sn_rule`。输出：唯一 SN 字符串。
- 固定前缀/文本：直接写进 `pattern` 的字面部分（如 `SN`、`NBK-`），不设占位符。
- 支持占位符：
  - `{YYYY}`（4 位年）、`{YY}`（2 位年）、`{MM}`（2 位月）、`{DD}`（2 位日）—— 取生成时的当前日期。
  - `{SEQ:n}`：n 位补零流水（如 `{SEQ:5}` → `00042`）。
  - `pattern` 中未被 `{...}` 包裹的字符按字面输出。
- 流水与重置：生成时计算"当前周期键"——`never`→固定串、`daily`→`YYYY-MM-DD`、`monthly`→`YYYY-MM`。若 `sn_rule.seq_period_key` 与当前周期键不同，`current_seq` 归零并更新 period_key；否则 `current_seq += 1`。整个读改写在一个事务内对该 sn_rule 行加锁（`SELECT ... FOR UPDATE`）保证并发唯一。
- 生成后校验：拼出的 SN 落库前确认 `serial_unit.sn` 无冲突（唯一索引兜底）。
- 非法 pattern（未知占位符 / `{SEQ}` 缺位数）在保存 sn_rule 时校验并拒绝。

### 5.2 过站校验（防跳站/防重复）
过站请求携带：成品 SN（首站可为空→生成）、工位（当前扫码工位）、可选组件列表、操作员。校验链：
1. 工单存在且状态为 `released` 或 `in_process`（`created`/`completed`/`closed` 拒绝）。
2. 定位 SN：若传入 SN 已存在 → 取之；若为首站且无 SN → 用规则生成新 SN（`status=in_process`，`current_step_seq=0`）。
3. 确定"期望的下一工序"：该 SN 在其 routing 上的 `current_step_seq` 之后的第一个 step。
4. **防跳站**：当前扫码工位必须等于期望下一工序的 `station_id`；否则拒绝（提示应到哪站）。
5. **防重复**：该 SN 在该 step 不能已有 `result=pass` 的 station_pass（返工场景例外，见 5.4）。
6. 组件绑定（若本次带了组件）：见 5.3。
7. 写 `station_pass(result=pass)`，更新 `serial_unit.current_step_seq/current_station_id`（乐观锁 `version`）。
8. 若为末工序 → `serial_unit.status=finished`，`work_order.produced_qty += 1`；若达 `qty` → 工单可转 `completed`。
9. 首个 SN 过站时若工单为 `released` → 自动转 `in_process`。
10. 发布 `StationPassed` 事件。
校验失败一律不写库，返回明确错误码 + 中文提示（HTMX 页面红色反馈）。

### 5.3 组件绑定（自由绑定 + 类型校验）
- 对本次提交的每个组件（`component_sn` 或 `component_batch_no` + `component_product_id`）：
  1. 校验该 `component_product_id` 在成品 active BOM 内；否则拒绝（"该组件不属于本产品 BOM"）。
  2. `track_mode=serial`：必须提供 `component_sn`，且该 SN 不得已被另一 active 成品占用（反查）。
  3. `track_mode=batch`：必须提供 `component_batch_no`（非空）。
  4. 写 `genealogy_bind(status=active, station_pass_id=本次过站)`。
- 绑定与过站在同一事务，任一失败整体回滚。

### 5.4 返工 / 拆解
- 触发：对某 SN 发起返工，指定"回退到的工序 seq"（`target_seq < current_step_seq`），可选"要解绑的组件绑定 id 列表"。
- 步骤（单事务）：
  1. `serial_unit.status = reworking`。
  2. 对指定的 genealogy_bind：置 `status=unbound`、记 `unbind_time/unbind_reason`（不物理删）。
  3. `serial_unit.current_step_seq = target_seq`（回退）。
  4. 发布 `SerialUnitReworkStarted` 事件。
- 之后该 SN 重新从 `target_seq` 之后过站（正常流程，5.2 的防重复对"该 step 已有 pass"在返工后允许再次过站——实现：防重复只看 `current_step_seq` 之后是否重复，回退后旧的 pass 记录保留但不阻挡）。返工重新过站时 `status` 由 `reworking` 复位为 `in_process`。
- 判废：`serial_unit.status = scrapped`（终态），不可再过站。

### 5.5 追溯查询
- **产品履历**：给 SN → station_pass 时间线 + genealogy_bind（active + 历史，标状态）。
- **物料正向**：给成品 SN → 全部 active 组件（SN/批次）；可选含已解绑历史。
- **物料反向**：给组件 SN 或批次号 → 装入的成品 SN 列表（含已拆解历史，标状态）—— 召回场景关键。

---

## 6. 前端（HTMX 扫码工位 + 最简主数据页）

- **扫码工位页**（核心）：选择/记住当前工位 → 扫成品 SN（或首站留空）→ 扫组件（可多个）→ 提交 → 服务端返回过站结果片段（绿=通过并显示 SN/下一站；红=错误原因）。所有交互 HTMX，无 SPA。片段模板复用 P0 已建的 `templates/partials/` 约定，`{{ }}` 自动转义防 XSS。
- **主数据页**：product / station / routing(+steps) / bom(+items) / sn_rule 的最简列表+新增+编辑。够配置起一条线。
- **WIP 看板页**：按工单/工位列出在制 SN 及其当前工序/状态（对 serial_unit 的查询）。
- **追溯查询页**：输入 SN 或组件批次/SN，展示履历与正/反向谱系。
- 第三方 JS 本地托管（沿用 P0 的 vendored htmx）。

---

## 7. 子计划切分（3 段顺序交付）

每段独立可跑、可复审、交付现场价值。各自走一遍 writing-plans → 实现循环。

**P1a — 主数据 + 工单 + SN 生成器**
- 模块：`masterdata`（product/station/routing/routing_step/bom/bom_item），`production` 起步（sn_rule、work_order、SN 生成器）。
- 交付：最简主数据 CRUD 页；能配置一条产线（产品+路线+工位+BOM+SN规则）；建工单、release；SN 生成器单测（规则/流水/重置/并发唯一）。
- 不含过站（SN 生成器可被单元测试直接调用验证）。

**P1b — 过站 + WIP**
- 模块：`production` 续（serial_unit、station_pass、过站服务、过站校验、WIP 查询），扫码工位页 + WIP 看板页。
- 交付：SN 首站生成 + 沿路线过站（防跳站/防重复）、状态流转、工单完工计数、WIP 可见。
- 依赖 P1a 的主数据与工单。发布 `StationPassed`。

**P1c — 追溯 + 谱系 + 返工**
- 模块：`trace`（genealogy_bind、绑定/解绑、正反向查询、履历、返工服务），过站页集成组件扫描绑定、追溯查询页、返工操作。
- 交付：过站时绑定组件、正/反向追溯、产品履历、返工/拆解闭环。
- 依赖 P1b 的过站（绑定挂在过站上）。

---

## 8. 库内事件（P1 新增）
在 P0 事件基础上，P1 真正开始 publish/subscribe：
- `WorkOrderReleased`、`SerialUnitCreated`、`StationPassed`、`SerialUnitFinished`、`SerialUnitScrapped`、`GenealogyBound`、`GenealogyUnbound`、`SerialUnitReworkStarted`
- MVP 内订阅关系保持轻量（如 trace 订阅 `StationPassed` 无强耦合动作即可；组件绑定在过站事务内同步完成而非靠事件，保证原子性）。事件主要为 P4+/AI 层预留扩展点，不在 MVP 制造跨模块异步复杂度。

---

## 9. 错误处理与并发
- 过站/绑定校验失败：明确错误码 + 中文提示，不写库。错误码集中定义便于前端与未来 API 消费者。
- 并发双扫：`serial_unit.version` 乐观锁；同一 SN 并发过站，后提交者版本冲突则重试或报错。
- SN 流水并发：sn_rule 行级锁（`FOR UPDATE`）。
- 唯一件重复装配：绑定时反查 active 占用。
- 边界校验只在入口（API/service 层），内部信任。

---

## 10. 测试策略
- 沿用 P0：真实 PostgreSQL 集成测试（`db_session` 回滚 fixture），TDD 优先。
- 重点覆盖领域逻辑：SN 生成（规则解析/流水/重置/并发唯一）、过站校验（防跳站/防重复/首站生成/末站完工）、组件绑定（BOM 类型校验/唯一件占用/批次）、返工（回退/解绑/重新过站）、追溯正反查（含已解绑历史）。
- 每段交付竖切须有对应测试并全绿方可视为完成。

---

## 11. 待办 / 开放问题（不阻塞 P1a，实现时敲定）
- SN `pattern` 校验的确切正则/解析实现细节，在 P1a 写生成器时定。
- 工位"记住当前工位"的实现（session / 本地存储 / URL 参数）在 P1b 扫码页时定。
- 追溯查询页展示深度（是否递归展开半成品的谱系树）——MVP 先做单层（成品→直接组件），多层递归留后。
- 操作员身份来源：MVP 复用 P0 auth 登录用户作为 operator；是否需要工位免登录/工牌扫码留后。

---

## 12. 下一步
本 spec 覆盖 P1 整体设计 + 3 段切分。经复核后，先为 **P1a（主数据 + 工单 + SN 生成器）** 走 writing-plans。P1b、P1c 待前一段跑通后各自立计划。
