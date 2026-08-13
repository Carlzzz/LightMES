# Issue / Andon 异常管理系统 - 设计文档

**日期**: 2026-08-13
**状态**: Approved
**关联**: 借鉴 OpenMES `Issue` + `IssueType` + `IssueAction` (CAPA)；填补 station 页现有 disabled「异常呼叫 (ANDON)」占位按钮；为后续 OEE/Downtime 模块提供"异常→停机"事件源

---

## 1. 背景与目标

### 1.1 现状

- Station 页底部有「异常呼叫 (ANDON)」按钮，但 `disabled title="暂未开放"`——纯占位
- Defect 模块管"产品质量不合格"（缺陷记录 + 处理决策），但管不了"过程被打断"（缺料、设备故障、安全问题、工装失效）
- 操作员遇到非质量类异常时，没有站内上报通道；主管只能口头/微信获知
- LightMES 原 roadmap P4（质量深化）/ P5（设备管理）需要"异常事件"作为数据源

### 1.2 目标

构建独立 Issue 模块覆盖：
- **手动上报**：operator 在 station 页点 ANDON 按钮一秒上报；supervisor 在列表页手动建（客户投诉、上游问题）
- **状态机闭环**：OPEN → ACKNOWLEDGED → RESOLVED → CLOSED + supervisor 可 REOPEN
- **CAPA 验证闸**：Issue 不能 CLOSE 直到所有 corrective/preventive/containment action 被验证
- **SN 级阻断**：`is_blocking=true` 且 OPEN/ACKNOWLEDGED 的 Issue 阻断绑定的 SN 过站
- **Defect 联动**：登记 defect 时可选「同时上报 Issue」，自动建关联 issue
- **AI Agent 工具**：4 个 MCP 工具接入 Agent Gateway

### 1.3 非目标（明确不做）

- ❌ 邮件 / Slack / 浏览器推送（v1 仅站内：station 横幅 + 列表页 + dashboard 卡片）
- ❌ 多租户（LightMES 是单厂单线部署）
- ❌ CustomField（LightMES 无此基础设施，Tier 3）
- ❌ 软删除 / 物理删除（Issue 一旦创建只走状态机；"作废"通过 reopen + 立即 close + resolution_notes 标注）
- ❌ 跨 Issue 关联 / 父子 Issue
- ❌ 文件附件（Media 模块是 Tier 3，不在范围）
- ❌ 自动从 first_inspection 失败建 Issue（避免一次性 OPEN 噪声；defect 联动已经覆盖质量侧入口）
- ❌ JSON API v1 endpoints（v1 HTML-only；如 ERP 后续要拉再加 `/api/v1/issues`）

---

## 2. 数据模型

### 2.1 新增表：`issue_types`（字典）

```python
class IssueType(Base, TimestampMixin):
    __tablename__ = "issue_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10))  # info|minor|major|critical
    is_blocking: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**CheckConstraint**: `severity IN ('info', 'minor', 'major', 'critical')`

**Seed（migration 内 bulk_insert）**:

| code | name | severity | is_blocking |
|---|---|---|---|
| `material_shortage` | 缺料 | major | true |
| `quality` | 质量异常 | major | false |
| `tool_failure` | 工装失效 | major | true |
| `equipment_fault` | 设备故障 | critical | true |
| `safety` | 安全问题 | critical | true |
| `other` | 其他 | minor | false |

### 2.2 新增表：`issues`（主表）

```python
class Issue(Base, TimestampMixin):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_type_id: Mapped[int] = mapped_column(
        ForeignKey("issue_types.id"), index=True)

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 状态机
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)
    # open|acknowledged|resolved|closed

    # 创建时从 type snapshot，可手改
    severity: Mapped[str] = mapped_column(String(10))  # info|minor|major|critical

    # 来源
    source: Mapped[str] = mapped_column(String(20), default="manual")
    # station_andon|defect_linked|manual

    # 业务上下文（皆 nullable，按场景填）
    serial_unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("serial_units.id"), nullable=True, index=True)
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_orders.id"), nullable=True, index=True)
    work_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), nullable=True, index=True)
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("operations.id"), nullable=True)
    defect_id: Mapped[int | None] = mapped_column(
        ForeignKey("defect_records.id"), nullable=True)

    # 时间戳 + 操作人
    reported_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reported_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now())
    acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Resolve 时填
    disposition: Mapped[str | None] = mapped_column(
        String(15), nullable=True)  # use_as_is|rework|scrap|hold
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    containment_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reopen 用
    reopen_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**CheckConstraints**:
- `status IN ('open', 'acknowledged', 'resolved', 'closed')`
- `severity IN ('info', 'minor', 'major', 'critical')`
- `source IN ('station_andon', 'defect_linked', 'manual')`
- `disposition IS NULL OR disposition IN ('use_as_is', 'rework', 'scrap', 'hold')`

**业务约束（service 层）**:
- `resolved_at` 非空 ⇔ `root_cause` + `containment_action` + `disposition` 都非空
- `closed_at` 非空 ⇒ 所有关联 IssueAction.status = 'verified'
- `status='closed'` 不允许直接删除/编辑核心字段，只能 reopen

### 2.3 新增表：`issue_actions`（CAPA）

```python
class IssueAction(Base, TimestampMixin):
    __tablename__ = "issue_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), index=True)

    type: Mapped[str] = mapped_column(String(15))  # corrective|preventive|containment
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(nullable=True)

    status: Mapped[str] = mapped_column(String(15), default="open")
    # open|in_progress|done|verified

    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**CheckConstraints**:
- `type IN ('corrective', 'preventive', 'containment')`
- `status IN ('open', 'in_progress', 'done', 'verified')`

**派生属性（不存 DB）**:
- `is_overdue` = `due_date < today AND status IN ('open', 'in_progress')`
- `is_blocking_close` = `status != 'verified'`

---

## 3. 状态机

### 3.1 转换图

```
   create ──► open ──ack──► acknowledged ──resolve──► resolved ──close──► closed
                 ▲                              │                       │
                 │                              │                       │
                 └────── reopen (sup+) ─────────┴───────────────────────┘
                                                              ▲
                                                  close 被拒如果
                                                  任一 CAPA != verified
```

### 3.2 转换表

| 转换 | 端点 | 必填字段 | 角色 |
|---|---|---|---|
| create | POST /issues | type / title | operator+ |
| ack | POST /issues/{id}/acknowledge | — | supervisor+ |
| resolve | POST /issues/{id}/resolve | root_cause / containment_action / disposition | supervisor+ |
| close | POST /issues/{id}/close | — （隐式要求所有 CAPA verified） | supervisor+ |
| reopen | POST /issues/{id}/reopen | reopen_reason | supervisor+ |
| add CAPA | POST /issues/{id}/actions | type / title | supervisor+ |
| CAPA: start | POST /actions/{id}/start | — | assignee 或 supervisor+ |
| CAPA: complete | POST /actions/{id}/complete | — | assignee 或 supervisor+ |
| CAPA: verify | POST /actions/{id}/verify | — | supervisor+ |

Service 层 `DomainError` 路径（新异常类定义在 `issue/service.py`，沿用 `shared/base.DomainError` 基类）：
- 非法转换（如 `closed → acknowledged`）→ `IssueStatusError`
- 缺必填（resolve 无 root_cause）→ `IssueValidationError`（DomainError 子类，HTTP 422）
- close 时 CAPA 未全 verified → `IssueHasUnverifiedActionsError`（DomainError 子类，HTTP 422）
- 操作员尝试 ack/resolve → 复用 `auth.PermissionError`（HTTP 403，路由依赖 + service 双层防护）
- station 阻断 → `IssueBlockError`（DomainError 子类，被 station_service 捕获后转 user-facing message）

### 3.3 is_blocking 派生

```python
def is_blocking(issue) -> bool:
    return issue.issue_type.is_blocking and issue.status in ("open", "acknowledged")
```

注意 `is_blocking` 来自 type，**不允许在 issue 级 override**（按确认决定 #2）。如果某个 issue 在特殊情况下要不阻断，supervisor 改用 `/issues/types` 调整 type（注意：**这是全局影响** —— 影响该 type 所有当前 OPEN/ACKNOWLEDGED 的 issue），或直接 acknowledge/resolve 让 issue 失去阻断性。

---

## 4. Station 集成（SN 级阻断）

### 4.1 阻断检查

`station_service.pass_station()` 在执行 PASS 前：

```python
from lightmes.modules.issue.service import check_block_for_sn

def pass_station(...):
    # 新增：检查 SN 是否被阻断
    blocking = check_block_for_sn(db, serial_unit_id)
    if blocking:
        raise IssueBlockError(
            f"该 SN 被 Issue #{blocking.id} 阻断：[{blocking.severity.upper()}] "
            f"{blocking.title}。请等待主管处置或访问 /issues/{blocking.id}"
        )
    # ... 现有 PASS 逻辑
```

`check_block_for_sn` 实现：

```python
def check_block_for_sn(db, serial_unit_id) -> Issue | None:
    """返回最新阻断 Issue，无则 None。"""
    return db.execute(
        select(Issue)
        .join(IssueType)
        .where(
            Issue.serial_unit_id == serial_unit_id,
            Issue.status.in_(["open", "acknowledged"]),
            IssueType.is_blocking.is_(True),
        )
        .order_by(Issue.id.desc())
        .limit(1)
    ).scalars().first()
```

### 4.2 station_view 模板改动

后端 `view` 字典增加：
```python
view["blocking_issue"] = check_block_for_sn(db, current_sn_id)  # None 或 Issue
```

模板（`production/partials/station_view.html`）在 `<div class="station">` 顶部插：

```html
{% if view.blocking_issue %}
<div class="alert alert--danger station-block-banner">
  ⛔ Issue #{{ view.blocking_issue.id }} 阻断中：
  [{{ view.blocking_issue.severity|upper }}] {{ view.blocking_issue.title }}
  · 状态: {{ view.blocking_issue.status }}
  · <a href="/issues/{{ view.blocking_issue.id }}">查看详情 →</a>
</div>
{% endif %}
```

### 4.3 ANDON 按钮启用

替换现有 disabled 按钮：

```html
<button type="button" class="btn-secondary"
        hx-get="/production/station/andon-form"
        hx-vals='{"work_station_id": "{{ work_station_id }}",
                  "serial_unit_id": "{{ view.sn_id or "" }}",
                  "work_order_id": "{{ view.work_order_id or "" }}",
                  "operation_id": "{{ view.current_op.id if view.current_op else "" }}'
        hx-target="#andon-modal-body"
        onclick="document.getElementById('andon-modal').style.display='flex'">
  异常呼叫 (ANDON)
</button>

<div class="modal" id="andon-modal" style="display:none">
  <div class="modal__body"><div id="andon-modal-body"></div></div>
</div>
```

### 4.4 Andon form endpoint

新增 `GET /production/station/andon-form`：

```python
@router.get("/production/station/andon-form")
def andon_form(work_station_id, serial_unit_id, work_order_id, operation_id,
               db, user):
    types = db.execute(
        select(IssueType).where(IssueType.is_active.is_(True))
        .order_by(IssueType.severity.desc())
    ).scalars().all()
    return templates.TemplateResponse("production/partials/andon_form.html", {
        "types": types,
        "ctx": {"work_station_id": ..., "serial_unit_id": ..., ...},
    })
```

表单提交 → POST /issues（source=station_andon）→ 返回 JS 关 modal + 触发 station 视图刷新（htmx trigger）。

### 4.5 实时性

**不做 WebSocket/SSE**。station 页每次 htmx swap 都会重渲染，阻断横幅跟着出。新建 blocking issue 后，操作员下次刷新或下次 htmx 操作即可见。和现有 station 行为一致（无 live push）。

---

## 5. Defect 联动

### 5.1 service 改动

`defect_service.create_defect()` 加 `create_issue: bool = False` 参数：

```python
def create_defect(..., create_issue: bool = False) -> DefectRecord:
    defect = ...  # 现有逻辑建 defect
    
    if create_issue:
        issue_service.create_from_defect(db, defect, reported_by_id=current_user.id)
        # 同事务，失败回滚
    
    db.commit()
    return defect
```

### 5.2 `issue_service.create_from_defect`

```python
SEVERITY_MAP = {"critical": "critical", "major": "major", "minor": "minor"}

def create_from_defect(db, defect, reported_by_id):
    quality_type = db.execute(
        select(IssueType).where(IssueType.code == "quality")
    ).scalar_one()
    issue = Issue(
        issue_type=quality_type,
        title=f"缺陷上报: {defect.defect_type.name} ({defect.quantity}件)",
        description=defect.description or "",
        status="open",
        severity=SEVERITY_MAP.get(defect.severity, "minor"),
        source="defect_linked",
        serial_unit_id=defect.serial_unit_id,
        work_order_id=defect.work_order_id,
        work_station_id=defect.work_station_id,
        defect_id=defect.id,
        reported_by_id=reported_by_id,
    )
    db.add(issue)
```

### 5.3 UI 改动

`quality/defect_log.html` 登记表单加 checkbox（**默认不勾**，per 用户决定 #3）：

```html
<label><input type="checkbox" name="create_issue" value="true">
  同时上报 Issue (Andon)
</label>
```

### 5.4 反向链接

- Issue 详情页：`source=defect_linked` 时显示 `Defect #${issue.defect_id}` 链接
- Defect 详情页（如有）：显示 `Issue #${issue.id}` 链接（query: defect_id = current.id AND source = 'defect_linked'）

---

## 6. 自动 #N 超链接

### 6.1 规则

任何 `description` / `resolution_notes` / `containment_action` / `root_cause` / `notes` 文本字段渲染时，跑 `#数字` → `<a href="/issues/N">#N</a>` 替换。

正则：`r'#(\d+)'` —— 只匹配纯数字（避免 `#ABC` 这种 hashtag）；上限 8 位（防止 `#123456789012345` 误匹配）。

### 6.2 Jinja2 filter

注册全局 filter（`main.py` 或 `templates` 配置）：

```python
import re
from markupsafe import Markup
_ISSUE_REF = re.compile(r'#(\d{1,8})(?!\d)')

def issue_linkify(text):
    if not text:
        return ""
    # 先 HTML 转义用户文本（防 XSS），再替换 #N（替换内容是固定安全模板）
    from markupsafe import escape
    escaped = escape(text)
    return Markup(_ISSUE_REF.sub(
        r'<a href="/issues/\1">#\1</a>', str(escaped)
    ))

# Jinja2Templates 配置：
templates.env.filters["issue_linkify"] = issue_linkify
```

模板用法：`{{ issue.description | issue_linkify }}`。filter 内部已处理转义，模板不要再用 `| safe`，否则双层。

### 6.3 应用字段清单

- Issue.title / description / resolution_notes / containment_action / root_cause / reopen_reason
- IssueAction.title / description / notes
- DefectRecord.description（顺手补）

station 阻断横幅不跑 linkify（已经是模板渲染的固定文本，无用户输入）。

---

## 7. UI 页面

### 7.1 `/issues` 列表页

- 路由：`GET /issues`
- 权限：operator（仅 `reported_by_id = self`）/ supervisor+（全部）
- 过滤栏：状态多选 / severity / source / work_station / work_order / 搜索（title/description 模糊）
- 表格列：`#ID` / 标题 / 类型 / severity badge / 状态 badge / SN / 上报人 / 上报时间 / 操作（查看）
- OPEN + is_blocking 行：左边框红色 4px + 「阻断」红色 badge
- 排序：默认 `status ASC, id DESC`（未关闭在前）

### 7.2 `/issues/types` 类型字典

- 路由：`GET /issues/types`
- 权限：admin only
- CRUD 表：code / name / severity / is_blocking / is_active / 操作
- 已被引用的 type 不允许删除（DB FK 阻挡），只能 `is_active=false`
- 顶部「新建 type」inline form

### 7.3 `/issues/{id}` 详情页

- 顶部：`#ID` / title / status badge / severity badge / source badge
- 关联上下文卡片：SN（链）/ WO（链）/ work_station（链）/ operation / defect（链，仅 source=defect_linked）
- 时间线：reported → acknowledged → resolved → closed 各时间戳 + 操作人
- 状态机按钮区（根据当前 status + 用户角色渲染）：
  - open + supervisor+ → 显示 acknowledge
  - acknowledged + supervisor+ → 显示 resolve（带表单：root_cause / containment_action / disposition / resolution_notes）
  - resolved + supervisor+ → 显示 close（如有未验证 CAPA，按钮 disabled + tooltip）
  - closed + supervisor+ → 显示 reopen（带 reopen_reason 表单）
- CAPA 区：列出所有 IssueAction，每行带状态 badge + 转换按钮（start/complete/verify，按角色）；底部「添加 CAPA」inline form
- 所有用户输入文本字段跑 `issue_linkify`

### 7.4 首页 dashboard 卡片

`home.html` 现有 4 列（主数据/生产执行/追溯/质量管理）→ 改为 5 列布局（或加新 row），新增「异常管理」卡片：

```html
<div class="card">
  <div class="card__title">异常管理</div>
  <div class="nav-grid">
    <a class="nav-card" href="/issues">
      <span class="nav-card__icon">🚨</span>
      <div class="nav-card__name">Issue 看板</div>
      <div class="nav-card__desc">未关闭: {{ open_count }} (阻断: {{ blocking_count }})</div>
    </a>
  </div>
</div>
```

`open_count` / `blocking_count` 由 home 路由 query 一次。

### 7.5 顶栏

`base.html` 加链接：

```html
<a class="app-bar__link" href="/issues">异常</a>
```

位置：放在「数采看板」之后、「退出」之前。

---

## 8. 权限

### 8.1 矩阵

| 操作 | operator | supervisor | admin |
|---|---|---|---|
| 创建 Issue（含 ANDON） | ✓ | ✓ | ✓ |
| 列表查看 | 仅 `reported_by_id = self` | 全部 | 全部 |
| 详情查看 | 仅自己上报的 | 全部 | 全部 |
| Acknowledge / Resolve / Close / Reopen | ✗ | ✓ | ✓ |
| 添加 CAPA | ✗ | ✓ | ✓ |
| CAPA: start / complete | assignee 或 supervisor+ | ✓ | ✓ |
| CAPA: verify | ✗ | ✓ | ✓ |
| IssueType 字典管理 | ✗ | ✗ | ✓ |

### 8.2 实现

- 路由层：`require_role("supervisor")` / `require_role("admin")` 依赖（复用现有 auth.dependencies）
- Service 层：operator 的"仅自己"过滤按 `current_user.role` 分支 query
- 双层防护：避免路由漏一道时 service 还能拦

---

## 9. AI Agent Gateway 工具

新增 4 个 MCP 工具，文件 `agent_gateway/tools/issues.py`：

| 工具名 | scope | 作用 |
|---|---|---|
| `list_issues` | read | 列出 issues（可按 status/severity/source/serial_unit_id 过滤，分页） |
| `get_issue` | read | 按 id 查 issue + 关联 CAPA |
| `create_issue` | write | 创建 issue（必填 type_code + title；可选 serial_unit_id 等） |
| `update_issue_status` | write | 触发状态转换（acknowledge/resolve/close/reopen） |

复用现有 `@require_scope("read"|"write")` + `get_http_request().state.db_session` 模式。

`schemas.py` 加 `IssueReadV1` / `IssueActionReadV1` / `CreateIssueResult` / `UpdateIssueStatusResult` Pydantic 模型。

---

## 10. Migration

### 10.1 Revision

- 文件：`src/lightmes/migrations/versions/<rev>_add_issue_andon.py`
- `down_revision = 'f2b8d4e97a1c'`（当前 HEAD：add_opcua_modbus_connections）
- `revision = '<新生成的 12 字符 hex>'`

### 10.2 操作

```python
def upgrade():
    # 1. 创建 3 张表（issue_types 先建，被 FK 引用）
    op.create_table("issue_types", ...)
    op.create_table("issues", ...)
    op.create_table("issue_actions", ...)

    # 2. 创建索引
    op.create_index("ix_issues_status", "issues", ["status"])
    op.create_index("ix_issues_serial_unit_id", "issues", ["serial_unit_id"])
    op.create_index("ix_issues_work_order_id", "issues", ["work_order_id"])
    op.create_index("ix_issues_work_station_id", "issues", ["work_station_id"])
    op.create_index("ix_issue_actions_issue_id", "issue_actions", ["issue_id"])

    # 3. Seed 6 个默认 type
    op.bulk_insert("issue_types", SEED_6_TYPES)

def downgrade():
    op.drop_table("issue_actions")
    op.drop_table("issues")
    op.drop_table("issue_types")
```

### 10.3 enum 处理

LightMES 既有 migration 用 `String + CheckConstraint`（不用 PG ENUM 类型，方便后续加值）。沿用此模式。

---

## 11. 文件改动清单

### 新建

- `src/lightmes/modules/issue/__init__.py`
- `src/lightmes/modules/issue/models.py`
- `src/lightmes/modules/issue/repository.py`
- `src/lightmes/modules/issue/service.py`
- `src/lightmes/modules/issue/schemas.py`
- `src/lightmes/modules/issue/router.py`
- `src/lightmes/modules/agent_gateway/tools/issues.py`
- `src/lightmes/templates/issue/list.html`
- `src/lightmes/templates/issue/detail.html`
- `src/lightmes/templates/issue/types.html`
- `src/lightmes/templates/issue/partials/issue_row.html`
- `src/lightmes/templates/issue/partials/action_row.html`
- `src/lightmes/templates/issue/partials/resolve_form.html`
- `src/lightmes/templates/issue/partials/reopen_form.html`
- `src/lightmes/templates/issue/partials/add_action_form.html`
- `src/lightmes/templates/production/partials/andon_form.html`
- `src/lightmes/migrations/versions/<rev>_add_issue_andon.py`

### 改动

- `src/lightmes/main.py`：注册 issue 模块 + 加 `issue_linkify` Jinja filter
- `src/lightmes/modules/production/defect_service.py`：`create_defect` 加 `create_issue: bool = False` 参数
- `src/lightmes/modules/production/router.py`：defect log POST 端点透传 `create_issue`；新增 GET `/production/station/andon-form`
- `src/lightmes/modules/production/station_service.py`：`pass_station` 调 `check_block_for_sn`，命中 raise `IssueBlockError`
- `src/lightmes/modules/production/service.py` 或 `repository.py`：`build_station_view` 增加 `blocking_issue` 字段
- `src/lightmes/templates/production/station_view.html`：阻断横幅 + ANDON 按钮启用 + modal 容器
- `src/lightmes/templates/quality/defect_log.html`：加「同时上报 Issue」checkbox
- `src/lightmes/templates/base.html`：顶栏加「异常」链接
- `src/lightmes/templates/home.html`：加「异常管理」卡片
- `src/lightmes/modules/agent_gateway/tools/__init__.py`：import `issues` 模块触发注册

---

## 12. 测试

### 12.1 单元测试（service 层）

- `test_issue_lifecycle.py`：open → ack → resolve（缺字段失败）→ close（带未验证 CAPA 失败）→ close（CAPA verified 后成功）→ reopen
- `test_issue_blocking.py`：阻断 SN pass 拦截 / 解除阻断后通过
- `test_defect_linkage.py`：`create_defect(create_issue=True)` → 同事务建 issue + defect_id 正确
- `test_capa_lifecycle.py`：start / complete / verify 各状态转换 + 权限检查
- `test_linkify.py`：`#N` 正则替换边界（`#ABC` 不替换 / `#123456789012345` 不替换 / `#47` 替换）

### 12.2 集成测试（router 层）

- `test_issue_router.py`：operator 只能看自己的 / supervisor ack/resolve/close 全套 / close 时 CAPA 未 verified 返回 422 + 正确 error code
- `test_station_andon.py`：GET andon-form → POST /issues → 阻断横幅出现 → pass 拦截 → supervisor resolve → pass 通过

### 12.3 MCP 工具测试

- `test_mcp_issues.py`：4 个工具 happy path + 错误码（NotFoundError / PermissionError）

---

## 13. 风险 & 备选

### 13.1 已识别风险

- **station 阻断导致生产线停摆**：如果 supervisor 不及时处置 blocking issue，SN 永远过不去。缓解：dashboard 卡片红字提示，supervisor 列表页 OPEN+blocking 行左边框红，足够显眼；不动 email/推送
- **operator 滥用 ANDON**：可能误报/恶作剧。缓解：上报记录 reported_by_id 留痕，supervisor 可在 list 页按上报人筛 + 直接 acknowledge+resolve 关掉
- **CAPA 拖延导致 issue 永不 close**：due_date + is_overdue 派生，列表页可排序；不上邮件提醒

### 13.2 关键判断

- **不加 email/Slack**：单厂内网部署，操作员 supervisor 同址办公，站内横幅+列表足够
- **不加 JSON API**：v1 HTML-only，避免双层 schema 维护成本；如 ERP 后续真要拉再加
- **Issue 不软删除**：合规需要"事件不可抹除"，作废走 reopen+close 流程留痕
- **is_blocking 不允许 issue 级 override**：避免"明明是 critical type 但被 mark non-blocking"的安全漏洞

### 13.3 未采纳的备选

- **operator 完全不能看 /issues 列表**（采纳 #2 后废弃）：operator 在 station 看自己上报的 issue 状态有用
- **defect 自动联动**（不勾选框也建 issue）：怕噪声，让用户选
