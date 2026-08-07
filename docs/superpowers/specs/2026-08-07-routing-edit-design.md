# 工艺路线编辑设计文档

- 日期：2026-08-07
- 状态：设计已获用户确认
- 上游：`2026-08-07-p2g-operation-workstation-many-to-many-design.md`（operation_work_stations 关联表 + allowed）、`2026-08-05-p2b-masterdata-ui-erp-sync-design.md`（active/inactive 状态、来源）
- 定位：补齐 P2b 主数据 UI 漏掉的"工艺路线编辑"——路线头 + 工序项皆可改、active 切换、物理删除；被工单引用时拒改。

---

## 1. 背景与目标

现状：`masterdata/routings.html` 只有"新建工艺路径"和只读列表，点击列表行无反应。RoutingRepository 只有 `get/list_all/get_active_by_product/operations_of`，无 update/delete。错误路线只能新建重建，无法直接修正。

本设计补齐编辑能力：
1. **路线头**可改 name、active/inactive 状态。
2. **工序项**可增/删/改（seq/code/name/default/allowed/skill/level）。
3. **删除路线**（物理删，级联清工序 + 关联表）。
4. **被工单引用时拒改/拒删**（防波及在制工单过站）。
5. **active 冲突校验**（切 active 时检查产品是否已有另一条 active）。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 编辑粒度 | 路线头（name/status）+ 工序项（全字段）皆可改 |
| 2 | 被引用保护 | 有工单引用该 routing_id → 拒改/拒删（提示"先处理工单"） |
| 3 | 删除方式 | 物理删除（未引用时），operations + operation_work_stations 跟随 CASCADE |
| 4 | active 切换 | 提供切换；切 active 时检查产品是否已有另一条 active（uq_active_routing_per_product 约束），有→拒 |
| 5 | 不做 | 版本管理、批量编辑、角色权限、复制重建（前轮否决） |

---

## 3. 数据模型 / 迁移

无新表、无新列、无迁移。复用：
- `routings` 表 + `status` (active/inactive) + `uq_active_routing_per_product`（P2b）
- `operations` 表（routing_id FK，无 ON DELETE 子句 → 默认 NO ACTION；删除路线前需手工删/级联 operations）
- `operation_work_stations`（operation_id FK `ON DELETE CASCADE`，删 operations 自动清关联表）

> **注意**：`operations.routing_id` FK 是 NO ACTION（默认），所以删 routing 前 service 必须先级联删该 routing 下所有 operations（删 operation 时 operation_work_stations 跟随 CASCADE，无需手动清）。删 routing 后无 operations 引用，FK 不会拦。

---

## 4. 服务层（`MasterDataService`）

**新增方法**：

- `update_routing_head(routing_id, name) -> Routing`：校验 routing 存在；name 非空；检查 `count_by_routing(routing_id) > 0` → 拒（"该路线已被 N 个工单引用，请先处理工单"）；更新 name。
- `set_routing_status(routing_id, status) -> Routing`：status 必须是 active/inactive；工单引用校验；切 active 时若产品已有另一条 active（`get_active_by_product(product_id)` 返回非自己）→ ValueError("该产品已有 active 路线 #{other_id}，请先设为 inactive")；更新 status。
- `update_operation(operation_id, *, seq, code, name, default_work_station_id, allowed_work_station_ids, required_skill_id, required_level, is_mandatory) -> Operation`：取 operation + 其 routing_id；工单引用校验（基于 routing_id）；default ∈ allowed 校验；allowed 非空 + 每个 ws 存在；seq 不与同 routing 其他工序冲突（`uq_operation_routing_seq`）；技能等级校验（沿用 P2c）；更新 operation 字段；**重写关联表**（删旧 allowed 关联 + 插新——`operation_work_stations` 是 CASCADE 不影响 operation 本身）。
- `add_operation(routing_id, *, seq, code, name, default_work_station_id, allowed_work_station_ids, required_skill_id, required_level, is_mandatory) -> Operation`：routing 存在；工单引用校验；seq 不重复；default ∈ allowed；插 operation + flush 拿 id + 写关联表。
- `delete_operation(operation_id) -> None`：取 operation + 其 routing_id；工单引用校验；删 operation（operation_work_stations 跟随 CASCADE）。
- `delete_routing(routing_id) -> None`：工单引用校验；先删该 routing 下所有 operations（关联表跟随 CASCADE）；再删 routing。

**WorkOrderRepository 新增**：
- `count_by_routing(routing_id: int) -> int`：`SELECT count(*) FROM work_orders WHERE routing_id = :r`。

**RoutingRepository 新增**：`delete(routing_id)`（直接 `db.delete(db.get(Routing, id))`）。
**OperationRepository 新增**：`update`（合并到 update_operation 内联）+ `delete(operation_id)` + `list_by_routing(routing_id)`（或复用 query_service.get_operations）。

工单引用校验约定：上述每个写方法内部第一步调 `count_by_routing(self_routing_id) > 0 → ValueError`（消息含工单数）。

---

## 5. 路由 / UI

**新路由**（`masterdata/router.py`，全 require_login）：
- `GET /masterdata/routings/{routing_id}` → 渲染 `masterdata/routing_detail.html`（路线头 + 工序表格 + 新增工序区 + 错误片段槽 + work_stations/skills 数据）。
- `POST /masterdata/routings/{routing_id}` → 改路线头（name + 可选 status），HTMX，hx-target `#result`。
- `POST /masterdata/routings/{routing_id}/status` → 单独切 active/inactive（独立按钮）。
- `POST /masterdata/routings/{routing_id}/operations` → 新增工序。
- `POST /masterdata/routings/{routing_id}/operations/{operation_id}` → 改工序（每行一个 form）。
- `POST /masterdata/routings/{routing_id}/operations/{operation_id}/delete` → 删工序。
- `POST /masterdata/routings/{routing_id}/delete` → 删路线（删后 302 回 `/masterdata/routings`）。

**详情页模板 `routing_detail.html`**（复用 `container--wide`）：
- 路线头卡片：显示 code/product（只读）；name 输入；active 切换按钮（按当前状态显示"设为 inactive"/"设为 active"）；保存头按钮；危险删除路线按钮（confirm）。
- 工序列表表格（每行一个 `<form>`）：seq/code/name/默认作业站下拉/允许作业站复选框组（复用 routings.html 的隐藏 input + JS 同步）/技能下拉/要求等级/操作列（保存本行 + 删除本行）。
- 底部"添加工序"卡片：空白工序 form，提交到 add_operation 端点。
- 错误/成功片段槽 `#result`。

**列表页入口**（`routings.html`）：列表 code 列改 `<a href="/masterdata/routings/{id}">{{ code }}</a>`，让点击进详情。

**XSS / 安全**：Jinja2 `{{ }}` 自动转义；HTMX 片段插值无 `|safe`；删除按钮 `onclick="return confirm('确认删除？')"` 防 misclick。require_login 守卫。

---

## 6. 子任务切分（4 个，顺序，各自 TDD + 复审）

1. **服务层 + 工单引用校验**：`WorkOrderRepository.count_by_routing`、`RoutingRepository.delete`、`OperationRepository.delete/list_by_routing`；`MasterDataService` 加 6 个方法（update_routing_head/set_routing_status/update_operation/add_operation/delete_operation/delete_routing），每个含工单引用校验 + 对应字段校验。测试：成功改/删 + 工单引用拒 + active 冲突拒 + default∉allowed 拒 + seq 冲突拒 + 物理删除级联（删 routing 后 operations + operation_work_stations 清）。
2. **API 路由 + 详情页 GET 渲染**：6 个路由处理器（GET 详情、POST 改头、POST 切状态、POST 加工序、POST 改工序、POST 删工序、POST 删路线）；GET 渲染 `routing_detail.html` 含 work_stations/skills 上下文；require_login 守卫；DomainError/ValueError → rollback + 红片段。测试：每个路由成功 + 失败路径 + 401。
3. **详情页 UI 模板 + 列表入口**：`routing_detail.html`（路线头可编辑卡片 + 工序表格每行 form + 允许复选框组 + 危险按钮 + JS）；`routings.html` code 列加详情链接。视觉测试（手动）。
4. **端到端 + 终审**：端到端测试（详情渲染 → 改头 → 切 active → 加工序 → 改工序 allowed → 删工序 → 删路线），工单引用拒端到端，active 冲突端到端；既有列表/创建回归；终审。

---

## 7. 测试策略

- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：
  - 工单引用拒：每个写操作（改头/切状态/改工序/加工序/删工序/删路线）在有 work_order 绑定时 raise ValueError 含工单数。
  - active 冲突拒：产品已有另一条 active 时 set_routing_status('active') 拒；切 inactive 总是允许。
  - default ∈ allowed 拒（编辑工序时改 default 不在 allowed）；allowed 空拒。
  - seq 冲突拒（改成同 routing 已有 seq）。
  - 物理删除级联：删 routing → operations 表清 + operation_work_stations 表清（CASCADE 验证）。
  - 详情页渲染：routing_detail 含所有工序 + allowed 预选中复选框。
  - 路由 401（未登录）+ 错误片段渲染。
  - 既有列表页 + 创建路由全绿。

---

## 8. 范围边界（不做，留后期）

- 版本管理（每次直接改，不留历史）。
- 批量编辑（每行单独保存）。
- 角色权限（沿用 require_login，不限角色）。
- 复制重建（"复制为草稿"已否决）。
- 既有 create 流程不改。
- 产品/路线/BOM 跨实体编辑（只做 routing）。

---

## 9. 下一步

本 spec 覆盖工艺路线编辑。经用户复审后走 writing-plans 出实现计划 → subagent-driven-development 执行 → 终审 → 合并。
