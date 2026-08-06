# P2d 设计文档：工位作业主界面

- 日期：2026-08-05
- 状态：设计已获用户确认
- 上游：`2026-08-07-p2a-hierarchy-traceability-design.md`（3 层模型 + operation 预留字段）、`2026-08-05-p2b-masterdata-ui-erp-sync-design.md`（主数据 UI 模式）、`2026-08-05-p2c-skill-qualification-design.md`（技能硬校验）
- 布局参考：`docs/design-refs/p2d-operator-station-reference.html`（Tailwind/FA CDN mockup，仅供布局灵感；本期用本地 app.css 重写，无 CDN）
- 定位：车间执行能力跃升的第四块，**集大成**——把 P2a 层级/路线 + P2b 主数据 + P2c 技能整合进一个操作员日常作业主界面。

---

## 1. 背景与目标

P2a/b/c 已把后端能力（3 层层级、工单/SN/产品、完整工序路径、物料绑定、参数采集、技能硬校验、过站推进）建齐，但操作员日常作业仍只有极简的 `/production/scan`。P2d 建一个富交互的工位作业主界面，让操作员在一屏内：扫码进入 → 看工艺路径全景（当前工序高亮）→ 绑当前工序物料 + 录参数 → 看技能资格状态 → 一键确认过站 → 重置扫下一单元。

**核心定位（已确认）**：真实集成主界面，构建在既有后端能力之上。**不是** mockup 全复刻（PLC 自动采集 / SOP PDF / ANDON / 申请跳站无后端），**不是**纯壳。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 定位 | 真实集成界面，基于既有后端；无后端的面板留占位/禁用，不接假数据 |
| 2 | 交互入口 | **新建专用路由** `/production/station`；既有极简 `/production/scan` 保持不变（两套交互模型分离） |
| 3 | 读写分离 | 三路由：`GET /production/station`（就绪页）+ `POST /station/load`（只读组装富界面）+ `POST /station/pass`（写过站） |
| 4 | 物料/参数提交 | **PASS 时一起提交**：前端持有 components+params，PASS 一次性提交给 pass_operation（贴合现有 OperationPassInput 单事务，无新中间态） |
| 5 | PASS 后行为 | **重置扫下一单元**（一个物理工位=路线里一道工序；单元流向下一站），非同单元续下一工序 |
| 6 | 样式 | 本地 app.css + HTMX，无 CDN（沿用 P1/P2 约定）；station 专属类 |
| 7 | 参数采集 | 手录表单（param_key/value/unit 多行）→ PASS 时经 ParamInput 提交；PLC 自动采集延后 |
| 8 | SOP 区 | 静态占位留白（本期不建 SOP 主数据表，operation.sop_id 保持预留）；SOP 主数据+PDF 延后独立阶段 |
| 9 | pass_operation | **不改**；operator_id 两处路由均服务端取 current_user 覆盖（防伪造） |

---

## 3. 数据模型与读模型

**无新表**（SOP 延后，operation.sop_id 保持预留）。

新增只读读模型 `StationView`（Pydantic），由新的只读 `StationService.load(scan, work_station_id, operator_id)` 组装——复用现有 query_service / genealogy / skill_service，不写库。

```python
class StationOpView(BaseModel):
    seq: int
    name: str
    code: str
    work_station_id: int
    status: str          # "done" | "current" | "future"

class StationComponentView(BaseModel):
    component_product_id: int
    component_code: str
    component_name: str
    qty: int             # BOM 需求量

class StationView(BaseModel):
    # 头部
    sn: str
    work_order_code: str
    product_code: str
    product_name: str
    operator_name: str
    operator_skill_level: int | None    # 当前工序所需技能的操作员等级(无要求=None)
    required_level: int | None          # 当前工序要求等级
    skill_ok: bool                       # 预判(过站仍以后端硬校验为准)
    is_off_station: bool                 # 当前工序不属于本工位
    # 路径全景
    operations: list[StationOpView]
    # 当前工序作业
    current_op: StationOpView | None
    components: list[StationComponentView]   # 当前工序 active-BOM 需绑定项
    sop_placeholder: bool = True             # 占位留白
```

**当前工序判定**：复用 `serial_unit.current_operation_seq`；operations 里 seq < current → done、== current → current、> current → future。`current_op.work_station_id != work_station_id` → `is_off_station=True`（前端只读提示；过站时三层防跳站兜底硬拦）。

**skill 预判**：current_op.required_skill_id 非空时调 `get_operator_level(operator_id, required_skill_id)` 填 operator_skill_level/required_level/skill_ok，仅用于界面提前标红；真正拦截仍在 pass_operation。

---

## 4. 三路由（读写分离）

```
GET  /production/station?work_station_id=<id>
     → 扫码就绪页（工位号 + 扫码输入框 → 提交到 /station/load）。
       页面守卫 current_user_or_none（未登录 401+HX-Redirect /login）。

POST /production/station/load   (HTMX, 只读)
     Form: work_station_id, scan(sn 或 work_order_code)
     → StationService.load(operator_id=current_user) 组装 StationView
       → 渲染富界面 partial(station_view.html)。
       解析失败(NotFoundError 等)→ rollback → 红色错误 partial。

POST /production/station/pass   (HTMX, 写)
     Form: work_station_id, scan, component_product_id[]/component_batch[],
           param_key[]/param_value[]/param_unit[]
     → 组 OperationPassInput(operator_id=current_user) → 复用 pass_operation()。
       成功 → "过站成功 + 重置扫下一单元"partial（含下一工序名/完工提示）。
       SkillError/BusinessRuleError/ConflictError/NotFoundError
         → rollback → 红色 partial（原样展示领域异常消息）。
```

`pass_operation` 不改。三层防跳站 / 乐观锁 / 技能硬校验全部沿用。operator_id 两处均服务端覆盖为 current_user（防伪造）。

---

## 5. UI 面板（本地 app.css + HTMX，无 CDN）

复刻 reference 布局，薄荷绿风格：
- **顶部状态栏**：当前 SN | 成品料号/工单 | 操作员+技能等级徽章（skill_ok 决定绿/红）| 重新扫码按钮。
- **工艺路径全景**：横向滚动，done=绿勾、current=高亮脉冲环+"当前"标、future=灰。JS 自动滚动当前工序居中（纯前端，从 reference 移植）。
- **左侧主区**：① 当前工序物料追溯——表格逐行列 BOM 需绑定组件（描述+需求量+扫码输入框+状态）；② 工艺参数采集——手录多行（param_key/value/unit），PLC 自动采集留白占位。
- **右侧辅助**：① SOP 面板=静态占位留白（"SOP 内容待建设"）；② 异常干预=返工（链接现有 /trace/rework）/ 跳站（无后端→禁用+"暂未开放"）。
- **底部操作条**：系统状态文案 | ANDON（无后端→禁用占位）| 大号 **确认过站(PASS)** 按钮，hx-post → /production/station/pass。

app.css 追加 station 专属类（`.station-*`），不动既有样式。写操作 require_login；HTMX 片段 `{{ }}` 自动转义。

---

## 6. 范围边界（明确不做，留后期）

- PLC 自动采集（参数手录）
- SOP 内容与 PDF（占位留白；operation.sop_id 保持预留）
- ANDON 呼叫（禁用占位）
- 申请跳站（仅硬防跳站存在；按钮禁用占位）
- 同单元续下一工序在屏（既定 PASS 后重置扫下一单元）
- 面板可插拔配置 operation.panels（保持预留，本期固定面板）

---

## 7. 子任务切分（4 个，顺序，各自 TDD + 复审）

1. `StationService.load` + `StationView` 读模型（只读组装，含当前工序判定 / skill 预判 / is_off_station）+ 单测。
2. 三路由（GET station / POST load / POST pass）+ require_login 守卫 + 领域异常 rollback + operator_id 服务端覆盖 + 页面测试。
3. UI 模板（station.html 就绪页 + station_view.html 富界面 + 结果/错误 partials）+ app.css station 样式 + 首页导航入口。
4. 端到端过站测试（扫码→load→bind+param→pass→重置；技能不足红拦；防跳站红拦）+ 终审。

---

## 8. 测试策略

- 真实 PostgreSQL 集成测试，TDD，逐任务复审 + 终审。
- 重点：
  - load 组装四态：正常 / 无技能要求 / skill 不足预判(skill_ok=False) / 非本站工序(is_off_station=True)
  - load 当前工序判定（done/current/future 分段正确）+ 当前工序 active-BOM 组件列出
  - pass 复用链路：成功推进（next_op/is_finished）、SkillError 拦、ConflictError（乐观锁）拦、防跳站拦
  - operator_id 服务端覆盖不可伪造（Form 传假 operator_id 被 current_user 覆盖）
  - 写接口 require_login（未登录 401+HX-Redirect）
  - 既有 /production/scan 不受影响（回归）

---

## 9. 下一步

本 spec 覆盖 P2d（工位作业主界面）。经确认后走 writing-plans 出实现计划 → subagent-driven-development 执行 → 终审 → 合并。P2d 完成后 P2 车间执行跃升四块（P2a 层级 / P2b 主数据+ERP / P2c 技能 / P2d 工位主界面）全部落地。
