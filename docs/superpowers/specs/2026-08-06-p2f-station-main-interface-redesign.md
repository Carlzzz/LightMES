# P2f 设计文档：工位主界面入口一站式重构

- 日期：2026-08-06
- 状态：设计已获用户确认
- 上游：`2026-08-05-p2d-operator-station-design.md`（富界面 station_view）、`2026-08-06-p2e-sn-lifecycle-carrier-design.md`（预生成+载体码+bind_and_pass_first）
- 定位：把当前分散的"选工单→扫载体码→后续站扫码"多步交互，收敛为"**作业站+工单+扫码 → 一次性进入富主界面**"的单页流程；首站扫载体码**只绑 SN、不过首工序**，由操作员看物料/SOP 后手动 PASS 才过站。

---

## 1. 背景与目标

P2e 落地了 SN 预生成 + 载体码过站，但当前 `/production/station` 入口分散：`select-wo`（选工单）+ `bind-and-pass`（扫载体码即绑+过站）+ `load`（后续站扫码）三个片段拼接。问题：
- 作业站、工单都是手输（作业站下拉刚加，工单仍是手输），看不到"本产线有哪些已下达工单"。
- 首站扫载体码当前 `bind_and_pass_first` 同时绑定+过首工序，跳过了"操作员看物料/SOP 后手动 PASS"的步骤——与参考布局（`docs/design-refs/p2d-operator-station-reference.html`）一致的人工确认语义不符。
- 多步交互、多次提交，操作员心智负担重。

P2f 收敛为：
1. **一张就绪页**：作业站下拉 + 工单下拉（联动本产线）+ 扫码框。
2. **一次提交进入富主界面**（P2d 已有的 `station_view.html`）：后端自动识别 SN / 载体码 / 首站新载体码；首站新载体码时**只绑定 SN**（写 `carrier_code`+`carrier_binding`，**不**过站、不生成 `OperationRecord`），让富界面在 pending 状态展示，操作员手动按 PASS 才过站。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 总体方案 | 单页 + 三栏入口 + 校验通过后 `station_view.html` 替换 `#station-root`（方案 A）。 |
| 2 | 首站扫载体码语义 | **只绑 SN、手动按 PASS 过站**（与参考布局一致）；`bind_and_pass_first` 拆为新 `bind_first_carrier`（仅绑定）+ 沿用 `pass_operation`。 |
| 3 | 工单选择 | **下拉**（`selectable_for_station(ws.line_id)`），仅 released/in_process 且本产线；作业站 change 时 JS 联动拉取 `<option>` 片段。 |
| 4 | 作业站选择 | 下拉（已实现），整页统一一个 select，两表单共享（已实现）。 |
| 5 | 三路 scan 判定 | SN 命中 → 走 load；活跃载体码命中 → 走 load；都不是 → 视为首站新载体码，调 `bind_first_carrier` 绑 SN 后走 load。 |
| 6 | 路由收敛 | 新增 `POST /production/station/enter` + `GET /production/station/work-orders`；**下线** `select-wo`、`bind-and-pass`（含其路由+模板+测试）。`load`/`pass` 保留。 |
| 7 | pass_operation | 不改；pending→in_process 由其内部完成（P2e 已实现）。 |
| 8 | StationService.load | 不改（已支持 SN/载体码识别）。 |
| 9 | 富主界面 | 复用 P2d `station_view.html`（顶部状态栏/工艺路径全景/物料绑定/参数手录/SOP占位/PASS底栏）。 |
| 10 | 范围边界 | 不建 SOP/PLC/ANDON/打印（P2d 占位）；pending 单元不出现在 WIP/追溯（P2e 已实现）。 |

---

## 3. 服务/路由变更

**CarrierService 拆分**（`production/carrier_service.py`）：
- **新 `bind_first_carrier(work_order_id: int, carrier_code: str, operator_id: int | None) -> SerialUnit`**：
  1. `first_pending_by_work_order(work_order_id)`；无 → `BusinessRuleError("工单 SN 已全部投产，请选择新工单")`。
  2. `get_active_by_carrier(carrier_code)`；非 None → `BusinessRuleError(f"载体码已绑定其他产品，请先解绑: {carrier_code}")`。
  3. `su.carrier_code = carrier_code`；`bindings.add(CarrierBinding(serial_unit_id=su.id, carrier_code=carrier_code, operator_id=operator_id))`。
  4. `self.db.flush()`；**不**调 `pass_operation`、**不**写 `OperationRecord`。返回 su（status 仍 "pending"）。
- **删除** `bind_and_pass_first`（其调用方 `enter` 路由改用 `bind_first_carrier` + 前端手动 PASS）。
- `unbind(scan, operator_id)` 不变（保留角色钩子注释）。

**`StationService.load` 不变**（已支持 SN→活跃载体码回退，P2e 已实现）。

**`OperationPassService.pass_operation` 不变**（pending→in_process 由其完成）。

**路由**（`production/router.py`）：
- **新 `POST /production/station/enter`**（HTMX，写）：Form `work_station_id, work_order_id, scan`；`current_user_or_none` 守卫（401+HX-Redirect /login，operator_id 服务端赋值防伪造）。判定 scan：
  - `get_by_sn(scan)` 命中 → `StationService.load(scan, ...)`。
  - 否则 `get_active_by_carrier(scan)` 命中 → `StationService.load(scan, ...)`（load 内部同样回退，但 enter 显式区分便于日志/未来扩展）。
  - 否则视为首站新载体码：`CarrierService(db).bind_first_carrier(work_order_id, scan, user.id)` → 再 `StationService.load(scan, ...)` 组装富界面（此时该载体码已被活跃绑定命中）。
  - 任何 DomainError → `db.rollback()` + 红片段（`partials/station_enter_error.html` 新建）含 `work_station_id`。
  - 成功 → 渲染 `station_view.html` 替换 `#station-root`。
- **新 `GET /production/station/work-orders?work_station_id=X`**（HTMX，只读）：守卫 `current_user_or_none`；`ws = get_work_station(X)`；若 ws None → 404 红片段；否则 `ProductionService.work_orders.selectable_for_station(ws.line_id)` + 对每条 `count_pending_by_work_order` 算剩余；渲染 `partials/station_wo_options.html`（仅 `<option>` 片段）。
- **删除** `POST /production/station/select-wo`、`POST /production/station/bind-and-pass` 路由及其模板 `station_wo_selected.html` / `station_bind_result.html`。
- **保留** `GET /production/station`（就绪页，重写模板）、`POST /production/station/load`（后续站富界面）、`POST /production/station/pass`（PASS 按钮目标）。

---

## 4. UI

**就绪页** `station.html`（重写）：
- 顶部卡片，并排三栏：① 作业站下拉（已实现，整页统一一个 select，JS 同步到表单 hidden）② 可用工单下拉（`hx-get="/production/station/work-orders"` 按 ① 联动，初始 URL 带入 `work_station_id` 时也触发）③ 扫码输入框。
- 一个"进入"按钮 → `POST /production/station/enter`，`hx-target="#station-root"`。
- 初始 `#station-root` 空；进入成功后由 `station_view.html` 替换。

**富主界面** `station_view.html`（P2d 既有，**复用不改**）：
- 顶部状态栏（当前 SN / 成品料号+工单 / 操作员+技能徽章 / 重新扫码按钮 hx-get `/production/station?work_station_id=...`）
- 工艺路径全景（done/current/future；pending 单元首过时 current=seq 第一道）
- 物料绑定表（BOM 组件，扫码输入框）+ 参数手录 + SOP 占位
- PASS 底栏 hx-post `/production/station/pass`，Form 带 `work_station_id` + `scan`（SN 或载体码）+ 组件/参数
- PASS 成功 → 既有 `station_pass_result.html` 重置片段（"✓ 已过工序 X，扫下一单元"）

**新 partials/station_enter_error.html**：红片段，显示错误 + "返回就绪页" 链接（带 `work_station_id`）。

**新 partials/station_wo_options.html**：仅 `<option value="..." ...>WO-XXX（产线·剩余 N）</option>` 片段。

---

## 5. 数据模型 / 迁移

无新表、无新列、无迁移。复用 P2e 的 `SerialUnit.carrier_code` + `CarrierBinding`。

---

## 6. 子任务切分（3 个，顺序，各自 TDD + 复审）

1. **CarrierService 拆分**：新 `bind_first_carrier`（只绑不过站）+ 删 `bind_and_pass_first`；`unbind` 不变。测试：bind_first_carrier 写 carrier_code+binding 且 status 仍 pending、无新 OperationRecord；用完/重复绑拦截；既有 unbind 解绑复用回归。
2. **路由收敛**：新 `POST /production/station/enter`（三路 scan 判定 + 防伪造 operator_id + DomainError rollback）+ 新 `GET /production/station/work-orders` 只读端点；删除 `select-wo` / `bind-and-pass` 路由、模板、旧测试。页面测试：未登录 401；首站扫载体码进入主界面（绑 SN、无 OperationRecord）；后续站扫 SN/载体码进入；工单无 pending 报错；载体码已绑报错；本产线过滤（异产线工单不可见）。
3. **就绪页 UI 改写**：`station.html` 三栏（作业站下拉+工单下拉+扫码）+ JS 联动（作业站 change 拉工单 options）+ `partials/station_wo_options.html` + `partials/station_enter_error.html`；移除 `station_wo_selected.html` / `station_bind_result.html`。端到端：选作业站→选工单→扫载体码→富界面渲染→手动 PASS→过站成功→重置扫下一单元；首站扫载体码后无 OperationRecord（证明"只绑不过站"）；下拉联动返回 option 片段。

---

## 7. 测试策略

- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：
  - **bind_first_carrier 只绑不过站**：调用后 SerialUnit status 仍 "pending"、carrier_code 已设、CarrierBinding 有活跃行、**OperationRecord 数=0**。
  - enter 三路判定：SN 命中、活跃载体码命中、首站新载体码绑定，均成功渲染 station_view。
  - enter 容器：工单 pending 用完 → BusinessRuleError"已全部投产"；载体码已被活跃单元占用 → BusinessRuleError"请先解绑"；未登录 401+HX-Redirect；operator_id 服务端赋值（Form 传假被忽略）。
  - work-orders 端点：仅返回 `selectable_for_station(ws.line_id)` 工单 + 剩余 pending 数；异产线工单不在结果；未登录 401。
  - UI 端到端：选作业站→工单下拉联动→扫载体码→富界面渲染（工艺路径 current=第一道）→ PASS → 过站记录生成 + 重置片段。
  - 既有 SN/载体码过站、技能硬校验、防跳站回归绿。

---

## 8. 范围边界（不做，留后期）

- SOP 内容与 PDF（占位）；PLC 自动采集（占位）；ANDON（禁用占位）；申请跳站（仅硬防跳站）；SN 标签打印功能/状态。
- 工单下拉的搜索/分页（本产线工单数量级一般个位数，下拉够用）。
- 主界面物料绑定的批量扫码加速、PLC 自动填值。

---

## 9. 下一步

本 spec 覆盖 P2f（工位主界面入口一站式重构）。经用户复审后走 writing-plans 出实现计划 → subagent-driven-development 执行 → 终审 → 合并。
