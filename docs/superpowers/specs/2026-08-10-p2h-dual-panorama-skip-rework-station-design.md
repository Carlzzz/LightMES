# P2h 设计文档：双层全景 + 工序级跳站 + 返工站位选择

- 日期：2026-08-10
- 状态：设计已获用户确认
- 上游：`2026-08-07-p2g-operation-workstation-many-to-many-design.md`（工序↔作业站多对多 + 连续过站）、`2026-08-06-p2f-station-main-interface-redesign.md`（一站式入口）、`2026-08-04-p1c-genealogy-rework-design.md`（返工基础流程）
- 定位：把工位作业富主界面的"全景"从单层路线扩展为双层（路线级 + 作业站级）；启用工序级跳站（operation_record.result="skip"）；返工发起时选定预期返工站位，首次 re-pass 时硬卡该站位。

---

## 1. 背景与目标

**P2g 后现状**：工位作业主界面（`/production/station`）已有单层"工艺路径全景"条，按 routing 全部工序展开，状态 `done`/`current`/`future`。但：

1. **全景只显路线全貌**，操作员看不到"本作业站能做哪些工序"的子集。多对多后一个站能做多道工序，操作员无感知。
2. **跳站按钮 disabled**（"暂未开放"）。实际生产中偶尔需要跳过非关键工序（如临时取消某道清洗/检验），无系统支持只能走线下流程。
3. **返工发起后操作员不知道去哪个站重做**。`rework.html` 只有 SN + target_seq + reason，提交后操作员凭经验找站，可能走错（特别是多对多后同一工序可在多站做）。

P2h 解决以上三点：
1. **双层全景**：Layer 2（作业站级，本站覆盖工序子集）放 Layer 1（路线级，既有）上方，同种圆点+连线视觉语言。
2. **工序级跳站**：supervisor 角色授权，写 `operation_record.result="skip"`，SN 推进到下一工序，跳过的工序不再补做（可后续返工重做）。
3. **返工站位选择（写库硬卡）**：返工发起时选定预期返工站位，写入 `SerialUnit.rework_target_station_id`；首次 re-pass 时硬卡该站位；重新发起返工可覆盖站位（易用性平衡）。

---

## 2. 关键决策（已确认）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 双层全景布局 | Layer 2（作业站级）放上方（操作员优先看本站能干什么），Layer 1（路线级，既有）放下方作全局参考。两层同种圆点+连线视觉语言。 |
| 2 | Layer 2 内容 | `op_ws_map` 中 `current_work_station_id ∈ allowed` 的工序子集，状态与 Layer 1 一致（含 `skipped`）。 |
| 3 | 新状态 `skipped` | operation_record 有 `result="skip"` 的工序显示为 `skipped`（灰色 + `⊘` 图标，区别于 `done` 的绿色 `✓`）。 |
| 4 | 跳站授权 | `supervisor` 角色强制（`admin` 也可）；普通 `operator` 403。沿用既有 RBAC。 |
| 5 | 跳站端点 | 新增 `POST /production/station/skip`，与 `/pass` 分离（语义清晰，验证链不同）。 |
| 6 | 跳站写记录 | `OperationRecord(result="skip", remark=reason)`，记录 operator_id/work_station_id 留审计。 |
| 7 | 跳站边界 | 末工序不可跳过（无下一工序可推进）；`is_mandatory=True` 工序跳过时允许（supervisor 已授权），remark 必填。 |
| 8 | 跳站事件 | 发布 `OperationSkipped`（新事件类，与 `OperationPassed` 平行，便于后续 Andon/异常模块订阅）。 |
| 9 | 返工站位写库 | `SerialUnit.rework_target_station_id: int \| None`（FK work_stations.id）。rework 时设值，首次 re-pass 后清空，非返工态恒 null。 |
| 10 | 返工站位校验 | `rework_service.rework()` 校验 `expected_repass_station_id ∈ 首个 re-pass 工序 allowed 集合`，防止设置无效站位。 |
| 11 | Re-pass 硬卡 | `pass_operation`：若 `su.status=="reworking"` 且 `su.rework_target_station_id is not None`（首次 re-pass），校验 `work_station_id == rework_target_station_id`，不符抛 `BusinessRuleError`。首次 re-pass 成功后清空字段。 |
| 12 | 易用性平衡 | 重新发起返工可覆盖站位：放宽 `rework_service` 校验，`status=="reworking"` 时允许 `target_seq == current_operation_seq`（仅重选站位）。不引入"取消返工"流程（YAGNI）。 |
| 13 | 后续 re-pass 不卡 | 首次 re-pass 完成后 `rework_target_station_id=None`，后续工序 re-pass 走正常 allowed 逻辑。 |
| 14 | 范围边界 | 不改 rework 语义（target_seq 仍是"回退到的已完工序"，首个 re-pass 工序 = `seq > target_seq` 第一道）；不引入 rework 历史表（既有 operation_records + genealogy_bind 已留痕）；不加 `operation_record.result` CHECK 约束（YAGNI）。 |

---

## 3. 数据模型

### 3.1 `SerialUnit` 新增字段（`production/models.py`）

```python
class SerialUnit(Base, TimestampMixin):
    # ... 既有字段不变 ...
    rework_target_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), default=None
    )
```

不变式：
- `status == "reworking"` 时该字段**应非 null**（rework 时设值）。
- 首次 re-pass 后清 null（`pass_operation` 清除）。
- `status in ("in_process", "finished", "scrapped", "pending")` 时该字段**应 null**。
- 不加 DB 级 CHECK（跨状态不变式，service 层保证）。

### 3.2 新迁移 `add_rework_target_station_to_serial_units.py`

```python
def upgrade():
    op.add_column(
        "serial_units",
        sa.Column("rework_target_station_id", sa.Integer(),
                  sa.ForeignKey("work_stations.id"), nullable=True),
    )
    # 数据迁移：现有 reworking 单元的 rework_target_station_id 留 null
    # （历史返工件无站位约束，操作员可在 allowed 任一站 re-pass）

def downgrade():
    op.drop_column("serial_units", "rework_target_station_id")
```

### 3.3 `operation_record.result` 不加约束

既有 string 字段，直接写 `"skip"`。不加 `CHECK (result IN ('pass', 'skip'))`（YAGNI，未来可能扩展 `'fail'` 等）。

### 3.4 Schema 变更（`production/schemas.py`、`trace/schemas.py`）

```python
# production/schemas.py
class StationOpView(BaseModel):
    operation_id: int  # 新增：用于 Layer 2 过滤
    seq: int
    name: str
    code: str
    work_station_id: int
    status: str  # done/current/future/skipped
    allowed_work_stations: list[str]
    # was_skipped 移除：status="skipped" 已表达；历史 skip 记录在 operation_records 可查

class StationView(BaseModel):
    # ... 既有字段不变 ...
    operations: list[StationOpView]  # Layer 1（既有）
    station_operations: list[StationOpView]  # 新增：Layer 2（本站工序子集）

class OperationSkipInput(BaseModel):
    work_station_id: int
    sn: str | None = None
    work_order_code: str | None = None
    operator_id: int | None = None
    reason: str  # 必填

class OperationSkipResult(BaseModel):
    sn: str
    skipped_op: OpInfo
    next_op: OpInfo | None
    is_finished: bool  # 恒 False（末工序不可跳）
    work_order_status: str
    next_op_can_continue_here: bool
```

```python
# trace/schemas.py
class ReworkInput(BaseModel):
    sn: str
    target_seq: int
    expected_repass_station_id: int  # 新增：必填
    unbind_bind_ids: list[int] | None = None
    reason: str | None = None
```

### 3.5 新事件（`production/events.py`）

```python
@dataclass
class OperationSkipped:
    serial_unit_id: int
    sn: str
    work_order_id: int
    operation_id: int
    work_station_id: int
    line_id: int
    reason: str
```

---

## 4. 服务层

### 4.1 `OperationPassService.skip_operation(data: OperationSkipInput) -> OperationSkipResult`

复用 `pass_operation` 的：
- SN/载体码/WO 定位（步骤 1+3）
- WO 状态校验（步骤 2）
- 3 层防跳站（步骤 5：作业站属工单产线 + 该工序 allowed 含本站）
- 乐观锁更新 `current_operation_seq`（步骤 6）
- SN 状态复位（步骤 10：`reworking`/`pending` -> `in_process`）
- 事件发布（步骤 11，发 `OperationSkipped`）

**跳过**：
- 技能校验（步骤 5b）
- BOM 累积校验（步骤 5c）
- 首检 hook（未来）
- 组件绑定（步骤 7）
- 参数录入（步骤 8）
- 完工逻辑（步骤 9，skip 永不完工）

**新逻辑**：
- 末工序不可跳：`expected == operations[-1]` -> `BusinessRuleError("末工序不可跳过")`
- 写 `OperationRecord(result="skip", remark=data.reason)`
- 推进 `current_operation_seq = expected.seq`（与 pass 一致）
- `next_op_can_continue_here` 计算同 pass

**授权**：路由层守卫（supervisor/admin），service 层不重复校验角色（信任路由守卫）。

### 4.2 `OperationPassService.pass_operation` 改动

在步骤 5（防跳站）之后、步骤 5b（技能校验）之前插入新步骤 5a（返工首次 re-pass 站位硬卡）：

```python
# 5a. 返工首次 re-pass 站位硬卡（仅在 reworking 态 + 已设预期站位时生效）
if su.status == "reworking" and su.rework_target_station_id is not None:
    if data.work_station_id != su.rework_target_station_id:
        expected_ws = self.query.get_work_station(su.rework_target_station_id)
        current_ws = self.query.get_work_station(data.work_station_id)
        raise BusinessRuleError(
            f"该返工件须在【{expected_ws.name if expected_ws else f'#{su.rework_target_station_id}'}】重做，"
            f"当前作业站【{current_ws.name if current_ws else f'#{data.work_station_id}'}】不符。"
            f"如需更改，请重新发起返工选择正确站位。")
```

在步骤 6（写 operation_record + 推进 seq）之后插入新步骤 6a（首次 re-pass 成功后清除返工站位约束）：

```python
# 6a. 首次 re-pass 成功后清除返工站位约束
if su.status == "reworking" and su.rework_target_station_id is not None:
    su.rework_target_station_id = None
```

**注意**：步骤 10 的 SN 状态复位（`reworking` -> `in_process`）发生在 `rework_target_station_id` 清除之后，不变式保持。

### 4.3 `StationService.load` 改动

新增查询：取该 SN 全部 `operation_records`，按 `operation_id` 分组取 `end_time` 最新的记录，构建 `latest_result_by_op: dict[int, str]`（operation_id -> "pass"|"skip"）。

`op_views` 状态判定逻辑（用最新记录的 result 决定 done vs skipped，覆盖被后续 re-pass 修正的 skip）：
```python
for o in operations:
    if o.seq > current_seq:
        st = "future"
    elif expected is not None and o.id == expected.id and su.status != "finished":
        st = "current"
    elif latest_result_by_op.get(o.id) == "skip":
        st = "skipped"
    else:
        st = "done"
```

`station_op_views` 构建（Layer 2，本站 allowed 子集）：
```python
station_op_views = [
    v for v in op_views
    if work_station_id in [w.id for w in op_ws_map.get(v.operation_id, [])]
]
```

`StationOpView` 新增 `operation_id: int` 字段用于此过滤。

### 4.4 `ReworkService.rework` 改动

`ReworkService.__init__` 新增 `self.query = MasterDataQueryService(db)`（既有未注入，本 spec 需要）。

签名扩展：
```python
def rework(
    self, sn: str, target_seq: int,
    expected_repass_station_id: int,
    unbind_bind_ids: list[int] | None = None,
    reason: str | None = None, operator_id: int | None = None,
) -> SerialUnit:
```

新逻辑：
1. **校验放宽（仅 reworking 态）**：
   - 原校验：`if target_seq < 0 or target_seq >= su.current_operation_seq: 拒绝`
   - 新校验：`if target_seq < 0 or target_seq > su.current_operation_seq: 拒绝`（`>=` 改 `>`）
   - 即：始终允许 `target_seq < current_operation_seq`（回退到更早的已完工序）；额外允许 `target_seq == current_operation_seq` **仅当** `su.status == "reworking"`（重选站位场景）。
   - 非 reworking 态（in_process/finished 等）若 `target_seq == current_operation_seq` 仍拒绝（rework 应是"回退"，不是"原地重做未做的"）。
2. **校验 expected 站 ∈ allowed**：
   ```python
   first_repass_op = next((o for o in operations if o.seq > target_seq), None)
   if first_repass_op is None:
       raise ValidationError("target_seq 之后无工序可重做")
   allowed = self.query.get_allowed_work_stations(first_repass_op.id)
   allowed_ids = [w.id for w in allowed] or [first_repass_op.default_work_station_id]
   if expected_repass_station_id not in allowed_ids:
       raise ValidationError(
           f"站位 #{expected_repass_station_id} 不在工序 "
           f"{first_repass_op.seq} {first_repass_op.name} 的允许集合内")
   ```
3. **写入**：`su.rework_target_station_id = expected_repass_station_id`（与 `current_operation_seq=target_seq`、`status="reworking"`、`version+1` 同一 update）。

---

## 5. 路由

### 5.1 新增 `GET /production/station/skip-form` + `POST /production/station/skip`（`production/router.py`）

`GET /production/station/skip-form`：返回跳站表单片段（HTMX 加载到模态框）。supervisor/admin 守卫，非授权角色返回 403 片段。

`POST /production/station/skip`：执行跳站。supervisor/admin 守卫。

```python
@router.get("/production/station/skip-form", response_class=HTMLResponse)
def station_skip_form(
    request: Request,
    work_station_id: int,
    scan: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if user.role not in ("admin", "supervisor"):
        return templates.TemplateResponse(request, "production/partials/station_enter_error.html",
                                           {"error": "仅主管/管理员可跳站"})
    # 加载 StationView 渲染表单（需 current_op 信息）
    view = StationService(db).load(scan, work_station_id, user.id)
    return templates.TemplateResponse(request, "production/partials/station_skip_form.html",
                                       {"view": view, "work_station_id": work_station_id})

@router.post("/production/station/skip", response_class=HTMLResponse)
def station_skip(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if user.role not in ("admin", "supervisor"):
        return templates.TemplateResponse(request, "production/partials/station_enter_error.html",
                                           {"error": "仅主管/管理员可跳站"})
    try:
        result = OperationPassService(db).skip_operation(OperationSkipInput(
            work_station_id=work_station_id,
            sn=scan,
            operator_id=user.id,
            reason=reason,
        ))
        db.commit()
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(request, "production/partials/station_enter_error.html",
                                           {"error": str(e.detail)})
    # 复用 pass 成功的三路分流渲染（finished / continue-here / switch-station）
    # 渲染 station_pass_result.html，与 pass 成功一致；区别是 passed_op 显示"已跳过"
    return _render_skip_result(request, db, result, work_station_id, user.id)
```

`_render_skip_result` 复用既有 `station_pass_result.html` 片段逻辑（finished 不可能，因为末工序不可跳；走 continue-here 或 switch-station 分支）。

### 5.2 新增 `GET /trace/rework/allowed-stations`（`trace/router.py`）

```python
@router.get("/trace/rework/allowed-stations", response_class=HTMLResponse)
def rework_allowed_stations(
    request: Request,
    sn: str,
    target_seq: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # 查 SN -> routing -> first_repass_op (seq > target_seq)
    # -> get_allowed_work_stations -> 渲染片段
    ...
```

### 5.3 `rework POST` 接收新字段

```python
@router.post("/trace/rework", response_class=HTMLResponse)
def rework_submit(
    request: Request,
    sn: str = Form(...),
    target_seq: int = Form(...),
    expected_repass_station_id: int = Form(...),  # 新增
    reason: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    ...
```

---

## 6. UI/模板

### 6.1 `station_view.html` 双层全景

Layer 2 插入 Layer 1 上方：
```html
<div class="station__path-wrap card">
  <div class="card__title">本作业站工序范围</div>
  <div class="station__path station__path--station" id="station-path-station">
    {% for o in view.station_operations %}
    <div class="station__step station__step--{{ o.status }}">
      <div class="station__step-node">
        {% if o.status == 'done' %}✓
        {% elif o.status == 'skipped' %}⊘
        {% else %}{{ o.seq }}{% endif %}
      </div>
      <div class="station__step-name">{{ o.name }}</div>
      {% if o.status == 'current' %}<div class="badge">当前</div>{% endif %}
    </div>
    {% endfor %}
  </div>
</div>

<div class="station__path-wrap card">
  <div class="card__title">工艺路径全景</div>
  <div class="station__path" id="station-path">
    <!-- 既有 Layer 1 内容，新加 skipped 状态分支 -->
  </div>
</div>
```

### 6.2 `station_view.html` 跳站按钮启用

按钮可见性按当前用户角色判定（模板需从路由注入 `current_user` 或 `can_skip: bool`）：

```html
{% if can_skip %}
<button type="button" class="btn-secondary" id="skip-btn"
        hx-get="/production/station/skip-form"
        hx-vals='{"work_station_id": "{{ work_station_id }}", "scan": "{{ view.sn or view.work_order_code }}"}'
        hx-target="#skip-modal-body"
        onclick="document.getElementById('skip-modal').style.display='flex'">
  申请跳站
</button>
{% else %}
<button type="button" class="btn-secondary" disabled title="仅主管/管理员可跳站">
  申请跳站
</button>
{% endif %}
```

跳站模态框：
```html
<div class="modal" id="skip-modal" style="display:none">
  <div class="modal__body">
    <div id="skip-modal-body"></div>
  </div>
</div>
```

路由层需向 `station_view.html` 注入 `can_skip: bool = (current_user.role in ("admin", "supervisor"))`。

跳站表单片段 `partials/station_skip_form.html`：
```html
<form hx-post="/production/station/skip" hx-target="#station-root" hx-swap="innerHTML">
  <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
  <input type="hidden" name="scan" value="{{ view.sn or view.work_order_code }}">
  <div class="alert alert--warning">
    确认跳过工序 {{ view.current_op.seq }} {{ view.current_op.name }}？
    跳过后该工序不再补做，可后续返工重做。
  </div>
  <label>跳站原因（必填）：<input name="reason" required></label>
  <button type="submit">确认跳站</button>
  <button type="button" onclick="closeModal('skip-modal')">取消</button>
</form>
```

### 6.3 `rework.html` 站位选择

```html
<form class="form-row" hx-post="/trace/rework" hx-target="#result" hx-swap="innerHTML">
  <div class="field"><label>成品 SN</label><input name="sn" placeholder="要返工的成品 SN" required></div>
  <div class="field"><label>回退到工序序号</label>
    <input name="target_seq" type="number" placeholder="如 1" required
           hx-get="/trace/rework/allowed-stations"
           hx-trigger="blur"
           hx-include="this"
           hx-target="#station-select"
           hx-swap="innerHTML">
  </div>
  <div class="field" style="flex:1"><label>返工原因</label><input name="reason" placeholder="可选"></div>
  <button type="submit">返工</button>
</form>
<div id="station-select"></div>
<div id="result" class="result-slot"></div>
```

片段 `partials/rework_allowed_stations.html`：
```html
{% if stations %}
<div class="field">
  <label>预期返工站位（必选）</label>
  <select name="expected_repass_station_id" required>
    <option value="">请选择</option>
    {% for s in stations %}
    <option value="{{ s.id }}">{{ s.name }}（{{ s.line_name }}）</option>
    {% endfor %}
  </select>
  <div class="nav-card__desc">将重做工序 {{ first_repass_op.seq }} {{ first_repass_op.name }}</div>
</div>
{% elif error %}
<div class="alert alert--danger">{{ error }}</div>
{% endif %}
```

### 6.4 `rework_success.html` 提示去哪个站

```html
<div class="alert alert--ok">
  SN {{ sn }} 已回退到工序 {{ target_seq }}，请前往
  <strong>{{ station_name }}</strong>
  重做工序 {{ first_repass_op_seq }} {{ first_repass_op_name }}。
</div>
```

### 6.5 CSS 新增（`static/css/app.css`）

```css
.station__step--skipped {
  background: #f0f0f0;
  color: #999;
  text-decoration: line-through;
}
.station__step--skipped .station__step-node {
  background: #ccc;
  color: #fff;
}
.station__path--station {
  /* 更紧凑，与 Layer 1 区分 */
  padding: 8px 12px;
  background: #f8fafb;
  border-radius: 6px;
}
.modal { /* 跳站模态框 */
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal__body {
  background: #fff; padding: 24px; border-radius: 8px;
  min-width: 400px; max-width: 600px;
}
```

---

## 7. 测试策略

### 7.1 单元测试

- `OperationPassService.skip_operation`：
  - 末工序跳过 -> `BusinessRuleError`
  - 正常跳过 -> `operation_record.result="skip"` + `current_operation_seq` 推进 + 事件发布
  - 非末工序跳过后 `is_finished=False`
- `OperationPassService.pass_operation` 返工站位硬卡：
  - `reworking` + `rework_target_station_id=S1` + `work_station_id=S1` -> 通过，字段清空
  - `reworking` + `rework_target_station_id=S1` + `work_station_id=S2` -> `BusinessRuleError`，字段保留
  - `reworking` + `rework_target_station_id=None` -> 不卡（后续 re-pass）
  - 非 reworking 态 -> 不卡
- `StationService.load`：
  - `latest_result_by_op` 查询正确（多记录取最新）
  - `station_operations` 仅含本站 allowed 工序
  - skipped 状态渲染（最新记录为 skip 时）
  - 被 re-pass 修正后的 skip 显示为 done（最新记录为 pass）
- `ReworkService.rework`：
  - `expected_repass_station_id` 不在 allowed -> `ValidationError`
  - `expected_repass_station_id` 在 allowed -> 写入 `rework_target_station_id`
  - `status=="reworking"` + `target_seq == current_operation_seq` -> 允许（重选站位）
  - `status=="reworking"` + `target_seq > current_operation_seq` -> 拒绝

### 7.2 集成测试

- 扫 SN -> 跳站 -> 验证 `operation_record.result="skip"` + `current_operation_seq` 推进 + Layer 2 显示 skipped
- 返工发起（选站位 S1）-> 验证 `rework_target_station_id=S1`
- 返工后 re-pass 在 S2 -> `BusinessRuleError`，字段保留
- 返工后 re-pass 在 S1 -> 通过，字段清空
- 重新发起返工（重选站位 S2）-> 验证字段覆盖
- 首次 re-pass 后二次 re-pass 在其他站 -> 通过（不再卡）

### 7.3 回归测试

- 既有 `pass_operation` 测试全绿（确认 skip 分离 + rework 硬卡不污染 pass）
- 既有防跳站/技能/BOM 校验测试全绿
- 既有返工测试适配新必填字段 `expected_repass_station_id`

### 7.4 E2E（HTMX）

- 工位页面点跳站 -> 模态框 -> 填原因 -> 确认 -> 看到推进到下一工序 + Layer 2 skipped 状态
- 返工页面输入 SN + target_seq -> blur 后看到站位下拉 -> 选站 -> 提交 -> 看到成功提示
- 在错误站 re-pass -> 看到红色错误 + 提示去正确站

---

## 8. 文件改动清单

| 文件 | 改动 |
|------|------|
| `src/lightmes/modules/production/models.py` | `SerialUnit` 新增 `rework_target_station_id` |
| `src/lightmes/migrations/versions/xxxx_add_rework_target_station.py` | 新迁移 |
| `src/lightmes/modules/production/operation_pass_service.py` | 新增 `skip_operation`；`pass_operation` 新增 5d 站位硬卡 + 6b 清字段 |
| `src/lightmes/modules/production/schemas.py` | `OperationSkipInput/Result`；`StationOpView.operation_id`；`StationView.station_operations` |
| `src/lightmes/modules/production/router.py` | 新增 `GET /production/station/skip-form` + `POST /production/station/skip`（supervisor 守卫）；`station` 路由向模板注入 `can_skip` |
| `src/lightmes/modules/production/station_service.py` | `load` 新增 `skipped_op_ids` 查询 + `station_op_views` 构建 |
| `src/lightmes/modules/production/events.py` | 新增 `OperationSkipped` |
| `src/lightmes/modules/trace/rework_service.py` | `rework()` 新增 `expected_repass_station_id` 参数 + allowed 校验；放宽 `target_seq == current_operation_seq` |
| `src/lightmes/modules/trace/schemas.py` | `ReworkInput` 新增 `expected_repass_station_id: int` |
| `src/lightmes/modules/trace/router.py` | 新增 `GET /trace/rework/allowed-stations`；`rework POST` 接收新字段 |
| `src/lightmes/templates/production/station_view.html` | Layer 2 全景条（Layer 1 上方）；跳站按钮启用 + 模态框；skipped 状态渲染 |
| `src/lightmes/templates/production/partials/station_skip_form.html` | 新片段 |
| `src/lightmes/templates/trace/rework.html` | target_seq onblur HTMX 取 allowed 站；站位下拉容器 |
| `src/lightmes/templates/trace/partials/rework_allowed_stations.html` | 新片段 |
| `src/lightmes/templates/trace/partials/rework_success.html` | 显示选中站名 + "请前往 X 站重做" |
| `src/lightmes/static/css/app.css` | `.station__step--skipped`、`.station__path--station`、`.modal` 样式 |

---

## 9. 后续工作（不在本 spec）

按推荐顺序：

1. **P2h（本 spec）** - 双层全景 + 跳站 + 返工站位选择
2. **首检接进过站** - 首检状态在 pass 时硬卡（hook 已在 StationService.load 探测，需在 pass_operation 加校验 + 放行流程）
3. **缺陷管理 + 不良品隔离** - 新 `defect_type`/`defect_record` 表 + SN 状态扩展 `quarantined` + 返工/报废/让步决策 + 缺陷发现页面
4. **P2i 工序级物料校验** - 新 `operation_bom` 关联表（operation_id × component_product_id × qty）+ `pass_operation` 物料校验从"仅末道累积"改为"每工序检该工序 BOM" + 主数据 UI

后续模块（12 模块标准对照后浮现）：
- 设备管理（⑧）：P3 采集 + P5 OEE/点检
- Andon/异常管理（⑪）
- 包装与物流（⑫）：SN->Carton->Pallet->Shipment
- 管理驾驶舱/报表：生产/质量/物料/设备报表
- 基础数据深化：客户/产品版本/仓库/班组
