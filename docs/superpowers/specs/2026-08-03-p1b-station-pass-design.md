# P1b 设计文档：过站 + WIP

- 日期：2026-08-03
- 状态：设计待用户书面复核
- 上游：`2026-07-31-lightmes-design.md`（总体）、`2026-07-31-p1-mvp-design.md`（P1 MVP，§5.2 过站校验、§7 三段切分）
- 前置：P0 工程地基 + P1a 主数据/工单/SN 生成器 已合并到 master

---

## 1. 目标与范围

P1 的第二段。在 P1a 主数据 + 工单 + SN 生成器之上，实现**过站主线**：SN 沿工艺路线过站，防跳站/防重复，首站按需生成 SN，末站完工计数，现场看得到在制状态（WIP）。手工/扫码驱动。

现场目标：一个能用的网页扫码工位——操作员选工位、扫工单/SN，即时看到过站结果（绿=通过、显示 SN 与下一站；红=明确错误原因）。

### 做
- `serial_unit`（产品单元/SN，承载 WIP 状态）与 `station_pass`（过站记录/履历）模型 + 迁移
- masterdata **只读查询 facade**（`MasterDataQueryService`）——供 production 读工艺路线有序工序等
- 共享**领域异常体系** + 统一 FastAPI 异常处理器
- `StationPassService`：过站校验链（工单状态、防跳站、防重复、首站生成 SN、末站完工、首过站翻转工单为 in_progress）
- 扫码工位页（HTMX）+ WIP 看板页（对 serial_unit 查询）
- 写接口复用 P1a 的 `require_login` 守卫
- SN 生成器双连接并发测试（P1b 首次真正消费 `next_sn`）

### 明确不做（YAGNI，留后）
- **组件绑定/物料谱系**（P1c）——过站服务预留绑定挂点但不实现
- **返工/拆解**（P1c）——本段 serial_unit 状态只做 in_process→finished / scrapped 的正向流转；reworking 状态列先建但无返工逻辑
- 不做工序级强制绑定、SPC、OEE、ERP、采集
- 不回头改 P1a 已有的裸 ValueError（技术债，留后收敛）

---

## 2. 关键实现决策（已定）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 跨模块读取 | **读服务 facade**：masterdata 暴露 `MasterDataQueryService`，production 只调它，不引用 masterdata models/repository。这是后续所有跨模块读取的标准约定，纠正 P1a 的 `db.get` 直读。 |
| 2 | 异常体系 | `shared/` 立 `DomainError` 基类 → `NotFoundError`(404)/`ConflictError`(409)/`ValidationError`(400)/`BusinessRuleError`(422)；统一 handler 映射为 HTTP 状态码 + 中文 detail。P1b 新代码用之；P1a 旧 ValueError 不回改。 |
| 3 | 过站入口 | 单一 `StationPassService`，HTMX 页面与 API 共用。 |
| 4 | SN 生成时机 | 首站过站时按需生成（非工单创建即批量生成）。 |
| 5 | WIP 表达 | 无独立 WIP 表；用 `serial_unit.status + current_step_seq + current_station_id` 查询。 |
| 6 | 并发 | serial_unit 乐观锁 `version` 防双扫；SN 流水行锁（P1a 已实现，本段加并发测试）。 |
| 7 | 组件绑定 | 不在 P1b；过站服务签名预留可选组件参数位但不实现绑定逻辑。 |

---

## 3. 架构落位

### 3.1 masterdata 只读查询 facade（新增，账 1）
在 masterdata 模块新增 `query_service.py`，暴露 `MasterDataQueryService(db)`，方法（P1b 需要的）：
- `get_routing(routing_id) -> Routing | None`
- `get_ordered_steps(routing_id) -> list[RoutingStep]`（按 seq 升序）
- `get_product(product_id) -> Product | None`
- （P1c 预留，本段不加）`get_active_bom(product_id)`
production 通过 `MasterDataQueryService` 读工序，**不 import masterdata 的 repository/models 到业务逻辑里**（读到的 RoutingStep 对象作为只读数据用；production 不写 masterdata 的表）。

> 说明：facade 返回 ORM 对象供只读使用是务实取舍——避免为 MVP 造一堆 DTO。约束是"下游只读、不写、不依赖对方 repository"。若未来耦合变痛再引入 DTO。

### 3.2 共享领域异常（新增，账 2）
`src/lightmes/shared/errors.py`：
```python
class DomainError(Exception):
    status_code = 400
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

class ValidationError(DomainError): status_code = 400
class NotFoundError(DomainError): status_code = 404
class ConflictError(DomainError): status_code = 409
class BusinessRuleError(DomainError): status_code = 422
```
`main.py` 注册一个 `@app.exception_handler(DomainError)`，返回 `JSONResponse(status_code=e.status_code, content={"detail": e.detail})`。HTMX 页面处理器自行捕获 `BusinessRuleError` 等并渲染红色片段（不走全局 handler，因为要返回 HTML 片段而非 JSON）。

### 3.3 production 模块扩展
新增 `serial_unit` / `station_pass` 模型、对应 repository、`StationPassService`、过站 API 与页面、WIP 查询。沿用 P0/P1a 全部约定（register、get_db 事务边界、response_model、真实 DB 测试、require_login 守卫、HTMX 自动转义）。

---

## 4. 数据模型（production 模块新增）

沿用 P1a 风格：`Mapped[]`/`mapped_column()`，继承 `Base`+`TimestampMixin`，默认含 id/created_at/updated_at。

**serial_unit（产品单元 / SN，承载 WIP 状态）**
- `sn`（唯一，索引）、`work_order_id`（FK work_orders.id）、`product_id`（FK products.id）
- `status`（`in_process`/`finished`/`scrapped`/`reworking`——本段只会置 in_process/finished/scrapped，reworking 列建好但 P1c 才用）
- `current_step_seq`（整数，当前已完成到的工序 seq；初始 0 表示尚未过任何站）
- `current_station_id`（FK stations.id，可空）
- `version`（乐观锁整数，default 0）
- 说明：首站过站时创建；WIP = 对本表按 work_order/station/status 过滤聚合，无独立表。

**station_pass（过站记录 / 履历）**
- `serial_unit_id`（FK）、`work_order_id`（FK）、`routing_step_id`（FK routing_steps.id）、`station_id`（FK stations.id）
- `operator_id`（FK users.id，可空）、`pass_time`（datetime，default now）、`result`（`pass`/`fail`）、`remark`（可空）
- 说明：某 SN 的履历 = 其全部 station_pass 按 pass_time 排序（P1c 追溯用；本段先落表 + 写入）。

新增迁移：`serial_units`、`station_passes` 两张表（FK 指向 P1a 的 work_orders/products/routing_steps/stations 与 P0 的 users）。

---

## 5. 过站校验链（StationPassService）

`StationPassService(db)`，核心方法 `pass_station(input) -> StationPassResult`。输入：`work_order_code`（首站用）或 `sn`（后续站用）、`station_id`（当前扫码工位）、`operator_id`（当前登录用户）、`components`（预留可选，本段忽略）。

经 `MasterDataQueryService` 读工序。校验链（单事务，任一步失败抛对应领域异常、不写库）：

1. **定位工单**：首站按 `work_order_code` 取工单；后续站按 `sn` 取 serial_unit 再取其工单。工单不存在 → `NotFoundError`。
2. **工单状态**：必须 `released` 或 `in_progress`（`created`/`completed`/`closed` → `BusinessRuleError` "工单状态不允许过站"）。
3. **定位/创建 SN**：
   - 有 `sn`：取 serial_unit；不存在 → `NotFoundError`；已 `finished`/`scrapped` → `BusinessRuleError`。
   - 无 `sn`（首站）：用工单的 sn_rule（无则报错提示先配规则）经 `SnGenerator.next_sn` 生成，建 serial_unit（status=in_process, current_step_seq=0）。
4. **确定期望下一工序**：工单 routing 的有序工序中，`seq > current_step_seq` 的第一个 step。若已无下一工序（SN 已到末站）→ `BusinessRuleError` "已完工，无后续工序"。
5. **防跳站**：当前 `station_id` 必须等于期望下一工序的 `station_id`；否则 `BusinessRuleError` "应到工位 X（工序 Y），当前工位不符"。
6. **防重复**：期望下一工序即"未过的下一站"，天然防重复（已过的工序 seq ≤ current_step_seq 不会再被选中）。额外保险：若该 step 已有该 SN 的 `result=pass` 记录 → `BusinessRuleError`。
7. **写过站**：写 `station_pass(result=pass)`；乐观锁更新 serial_unit（`current_step_seq=该step.seq`、`current_station_id`、`version+1`）。乐观锁冲突（并发双扫）→ `ConflictError` "该产品正被其他工位处理，请重试"。
8. **末站完工**：若该 step 是最后一道工序 → `serial_unit.status=finished`；`work_order.produced_qty += 1`；若 `produced_qty >= qty` → 工单 `status=completed`。
9. **翻转工单为在制**：若工单当前为 `released`，本次过站成功后置 `status=in_process`（等价于"首个过站动作触发"，因为只有第一次过站时工单还停在 released；之后已是 in_process）。
10. **发布事件** `StationPassed`（trace 等将来订阅；本段发布即可，无强订阅动作）。

返回 `StationPassResult`：`sn`、`passed_step`（seq+name）、`next_step`（seq+name+station，或 None 表示已完工）、`is_finished`、`work_order_status`。

---

## 6. WIP 查询

`StationPassService`（或独立 `WipService`）提供：
- `wip_by_work_order(work_order_id) -> list[serial_unit 摘要]`：该工单下所有 in_process 的 SN 及其当前工序/工位。
- `wip_by_station(station_id) -> list[...]`：当前停在某工位的在制 SN。
- 摘要含 sn、current_step_seq、current_station、status。纯查询，无写。

---

## 7. 前端（HTMX）

- **扫码工位页** `/production/scan`：选当前工位（下拉，记住选择用 query 参数或 session）→ 输入框扫工单号（首件）或 SN → 提交 → 服务端返回过站结果片段：绿色（SN、已过工序、下一站）或红色（`BusinessRuleError.detail` 中文原因）。整个交互 HTMX，片段模板 `{{ }}` 自动转义。
- **WIP 看板页** `/production/wip`：按工单或工位列出在制 SN 及当前工序/状态。
- 写操作（过站 POST）复用 `require_login`（未登录 → HX-Redirect /login，同 P1a 页面约定）。
- 第三方 JS 用 P0 本地托管的 htmx。

---

## 8. 库内事件

本段真正发布：`SerialUnitCreated`、`StationPassed`、`SerialUnitFinished`、`SerialUnitScrapped`。MVP 内无强订阅动作（末站完工计数在过站事务内同步完成，不靠事件），事件为 P1c/P4/AI 层预留扩展点。

---

## 9. 错误处理与并发
- 过站校验失败：抛对应领域异常（多为 `BusinessRuleError`，422 + 中文），API 走全局 handler 返回 JSON，HTMX 页面捕获后渲染红色片段。均不写库。
- 并发双扫同一 SN：serial_unit 乐观锁 `version`，冲突抛 `ConflictError`。
- SN 流水并发：P1a 行锁（`FOR UPDATE` + populate_existing），本段加双连接并发测试证明。
- 边界校验只在入口（service），内部信任。

---

## 10. 测试策略
- 沿用真实 PostgreSQL 集成测试（`db_session` 回滚 fixture），TDD。
- 重点覆盖：`MasterDataQueryService`（有序工序）、领域异常→HTTP 映射、过站校验链每个分支（首站生成 SN、正常推进、防跳站、防重复、工单状态拒绝、末站完工与工单 completed、首过站翻转 in_progress）、乐观锁并发双扫、SN 生成器双连接并发、WIP 查询、扫码页 + WIP 页（TestClient + require_login）。
- 每段竖切须有对应测试并全绿。

---

## 11. 待办 / 开放问题（不阻塞实现）
- 扫码页"记住当前工位"的具体载体（query 参数 / session）在实现时定；倾向 query 参数（工位机固定 URL 即可）。
- 乐观锁冲突后是否自动重试一次：MVP 先直接报 `ConflictError` 让操作员重扫，不做自动重试。
- 末站判定：以"routing 中最大 seq 的工序"为末站；若未来支持并行/分支工序再扩展（MVP 线性工序）。

---

## 12. 下一步
本 spec 覆盖 P1b（过站 + WIP）。经复核后走 writing-plans 出 P1b 实现计划。P1c（追溯 + 谱系 + 返工）待 P1b 跑通后另立。

