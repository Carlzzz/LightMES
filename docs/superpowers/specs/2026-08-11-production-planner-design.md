# 生产计划模块（Production Planner）- 设计文档

**日期**: 2026-08-11
**状态**: Approved
**关联**: 借鉴 OpenMES（`C:\Users\zhaocao\Documents\GitHub\OpenMes`）的 Planner + UI 风格

---

## 1. 背景与目标

### 1.1 现状

LightMES 当前 `WorkOrder` 已有 `planned_start` / `planned_end` 字段，但：
- 无 Planner UI（无排程界面）
- 无 Shift 概念
- 无 conflict 检测
- 无变更日志/undo
- 无 Backlog 视图

### 1.2 目标

借鉴 OpenMES（Laravel/React 大型 MES）的 Planner 设计，构建 LightMES 风格的工单排程系统：

- **周视图**：产线 × 7 天网格，drag-drop 排程
- **日视图**：产线 × 24 小时 Gantt，分钟级 resize
- **冲突检测**：同产线时间重叠硬拦截
- **变更日志 + undo**：排程可追溯、可回退
- **完整 Shift 模型**：含 days_of_week、per-line 绑定
- **OpenMES 风格**：白底/蓝色 accent，**仅 Planner 页**应用（其他页面下个 spec 统一刷新）

### 1.3 非目标（明确不做）

- 月视图（周+日已足够）
- 多产线 WorkOrderPlacement（一单一产线）
- 优先级规则引擎（priority int 手动设置即可）
- Capacity heatmap / OEE 集成（OEE 独立 spec）
- Real-time polling（HTMX 轮询后续加）
- Maintenance 事件叠加（无设备模块）
- CSV 导入工单（独立 spec）
- 全局 UI 风格统一刷新（下个 spec）

---

## 2. 范围与 UI 策略

### 2.1 本 spec 范围

✅ **包含**：
- Planner 模块（功能完整）
- Planner 页 OpenMES 风格（白底/蓝 accent/宽敞间距）
- 新增 CSS token 命名空间 `.planner-*`（不污染现有页面样式）
- Self-host Inter font（保留"无 CDN"原则）

❌ **不包含**（下一个 spec）：
- 顶部栏白底化
- 基础数据/过站/质量等已有页面样式重做
- 全局 token 迁移

### 2.2 风格对照（Planner 页）

| 元素 | 现有 LightMES | Planner 新风格 |
|---|---|---|
| 背景 | `--bg: #f1f5f9` | `--p-bg: #f9fafb` |
| 顶部栏 | indigo 渐变（保留全局） | 同左（不动） |
| 卡片 | 白底 + 1px border + shadow-1 | 白底 + 1px `#e5e7eb` + 更轻 shadow |
| 主色 | `--blue-600: #2563eb` | `--p-accent: #3b82f6` |
| 字体 | Segoe UI 15px | Inter 14px (fallback Segoe UI) |
| WO 卡状态色 | 走 `--ok/--danger/--warning` | pending=灰/in_progress=蓝/done=绿/overdue=红 |

### 2.3 路由

- `/production/planner` — Planner 主页（周视图）
- `/production/planner/daily?date=YYYY-MM-DD` — 日视图
- `/production/planner/work-orders/{id}/schedule` — POST 排程（HTMX 表单）
- `/production/planner/work-orders/{id}/unschedule` — POST 移除排程
- `/production/planner/changes/{log_id}/undo` — POST undo
- `/api/production/planner/work-orders/{id}/schedule` — PATCH JSON 排程（含 force_conflict）
- `/production/shifts` — 班次 CRUD（admin/supervisor）

---

## 3. 数据模型

### 3.1 新增表

#### `shifts` — 班次

```python
class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"
    __table_args__ = (
        UniqueConstraint("code", name="uq_shift_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column()                    # "S1" / "EARLY"
    name: Mapped[str] = mapped_column()                    # "早班"
    start_time: Mapped[str] = mapped_column()              # "06:00" (HH:MM)
    end_time: Mapped[str] = mapped_column()                # "14:00"（end < start 表示跨夜）
    days_of_week: Mapped[list | None] = mapped_column(JSON, default=None)
        # [1,2,3,4,5] = Mon-Fri；NULL = 每天
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("lines.id"), default=None)              # NULL = 全局班次
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
```

业务约束：
- 同产线（含 NULL 全局）下 `code` 唯一
- 跨天判定：`end_time < start_time` 表示跨夜（如夜班 22:00→06:00）
- `days_of_week` 用 ISO 8601 序号（1=Mon ... 7=Sun）

#### `schedule_change_logs` — 排程变更日志

```python
class ScheduleChangeLog(Base, TimestampMixin):
    __tablename__ = "schedule_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    action: Mapped[str] = mapped_column()                  # schedule / unschedule / move / undo
    before: Mapped[dict | None] = mapped_column(JSON, default=None)
        # {line_id, planned_start, planned_end}
    after: Mapped[dict | None] = mapped_column(JSON, default=None)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    undone_from_log_id: Mapped[int | None] = mapped_column(default=None)
```

业务约束：
- 每次 Planner 编辑前后快照入 log
- `undo` 把 `before` 写回 WO，并标记原 log `undone_at`
- 最近 50 条可回看 + 可 undo

### 3.2 已有表扩展

#### `work_orders` 新增列

```python
priority: Mapped[int] = mapped_column(default=5)            # 1-9，越大越紧急
```

`planned_start` / `planned_end` 已存在，本 spec 直接复用。

### 3.3 Alembic 迁移

```python
def upgrade():
    # 1. shifts 表
    op.create_table('shifts', ...)
    op.create_unique_constraint('uq_shift_code', 'shifts', ['code'])

    # 2. schedule_change_logs 表
    op.create_table('schedule_change_logs', ...)

    # 3. work_orders 加 priority
    op.add_column('work_orders',
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'))

def downgrade():
    # 反向操作
```

### 3.4 业务约束（service 层）

| 约束 | 实现 |
|---|---|
| 同产线 WO 时间窗不可重叠 | `PlannerService.detect_conflict` 查询 |
| `planned_end > planned_start` | service 层校验 |
| `priority` 1-9 | Pydantic schema 校验 |
| Shift `start_time/end_time` 格式 HH:MM | Pydantic schema 校验 |
| Shift `days_of_week` 元素 1-7 | Pydantic schema 校验 |
| ScheduleChangeLog `action` 取值集 | service 层枚举检查 |

---

## 4. Planner 交互

### 4.1 周视图（默认 `/production/planner`）

**布局**：
- 左侧固定 240px 边栏：未排程工单 backlog（drag source）
- 顶部固定工具栏：周导航 + 视图切换 + 新建按钮
- 主网格：每行 = 一条产线，每列 = 一天，每格可放多张 WO 卡

**WO 卡状态色**（OpenMES 风格，按 WO 状态字段计算）：

| 卡片状态 | 触发条件 | 视觉 |
|---|---|---|
| `pending`（已排未开） | `status in ('created','released')` AND `produced_qty == 0` AND `planned_end >= now` | 白底 + 灰边 |
| `in_progress`（生产中） | `produced_qty > 0` AND `produced_qty < qty` | 蓝底（`#3b82f6`）+ 白字 |
| `done`（完工） | `produced_qty >= qty` | 绿底 + 删除线 |
| `overdue`（超期未完） | `planned_end < now` AND `produced_qty < qty` | 红边 + 加粗 |

**注**：`blocked`（阻断）状态本 spec 不做，Andon 模块加入时扩展。

### 4.2 周视图交互（HTML5 drag-drop）

| 操作 | 行为 |
|---|---|
| 从 backlog 拖 WO 卡 → 网格某格（line × day） | 弹出对话框确认时长（默认 8h）→ planned_start=day 08:00 / planned_end=day 16:00 → POST 排程 |
| 拖网格内的 WO 卡到另一格 | 改 line_id 和 planned_start/end 的日期部分，时间部分保留 |
| 点 WO 卡 | 弹详情/编辑浮层：手动改 start/end、priority、移除排程（回 backlog） |
| 右键 WO 卡 | 上下文菜单：复制到下周、查看历史变更 |

### 4.3 日视图（`/production/planner/daily?date=YYYY-MM-DD`）

**布局**：
- 左侧 120px：产线名
- 顶部：24 小时刻度（每小时 60px，可滚动）
- 网格：WO 卡按 `planned_start/end` 绝对定位
  - `left = (start_hour - day_start) * 60px`
  - `width = duration_min * 1px`
- 底部：当天 Shift 带状显示（背景色 + label）

### 4.4 日视图交互

| 操作 | 行为 |
|---|---|
| 拖 WO 卡左右移动 | 改时间，duration 不变；snap-to 15 分钟 |
| 拖 WO 卡右边缘 resize | 改 duration（最小 30 分钟，snap 15 分钟） |
| 拖 WO 卡跨产线 | 改 line_id（触发冲突检测） |
| 双击空白 | 创建新工单，默认时长 1h |

### 4.5 冲突检测（硬拦截）

```python
def detect_conflict(db, line_id: int, start: datetime, end: datetime,
                    exclude_wo_id: int | None) -> WorkOrder | None:
    """同产线、状态活跃、时间窗重叠 → 返回冲突 WO"""
    return db.execute(
        select(WorkOrder).where(
            WorkOrder.line_id == line_id,
            WorkOrder.id != exclude_wo_id if exclude_wo_id else True,
            WorkOrder.status.in_(("created", "released", "in_progress")),
            WorkOrder.planned_start < end,
            WorkOrder.planned_end > start,
        )
    ).first()
```

**触发点**：
- 排程 API（POST `/production/planner/work-orders/{id}/schedule`）
- 移动 API（PATCH 改 line/start/end 时）

**冲突时**：
- 返回 409 Conflict + `{conflict: true, conflict_wo_id, conflict_wo_code}`
- 前端 HTMX 弹错误模态："此产线该时段已被工单 WO-X 占用"
- 提供 `force_conflict=true` 选项（supervisor 角色）跳过

### 4.6 ScheduleChangeLog 记录

每次排程/移动/移除操作：
1. 操作前快照 `before = {line_id, planned_start, planned_end}`
2. 操作后快照 `after = {...}`
3. 写入 `ScheduleChangeLog(work_order_id, user_id, action, before, after)`
4. 发事件 `WorkOrderScheduled(wo_id, fields_changed)`

### 4.7 Undo 支持

`POST /production/planner/changes/{log_id}/undo`：
1. 找 log，校验 `undone_at is None`
2. 把 `log.before` 写回 WO（再次校验冲突 — 若 before 时间窗已被占，拒绝并报错）
3. 标记 log `undone_at = now()`
4. 写新 log：action=undo, before=current, after=restored, undone_from_log_id=log.id

UI：Planner 右侧底部"最近变更"抽屉，列最近 50 条 + undo 按钮。

---

## 5. UI 样式落地

### 5.1 CSS 命名空间（不污染现有页面）

新增 `src/lightmes/static/css/planner.css`（独立文件，仅 Planner 页加载）：

```css
.planner-root {
  --p-bg:        #f9fafb;
  --p-surface:   #ffffff;
  --p-border:    #e5e7eb;
  --p-border-h:  #d1d5db;
  --p-text:      #111827;
  --p-text-soft: #6b7280;
  --p-accent:    #3b82f6;
  --p-accent-h:  #2563eb;

  --p-pending:   #e5e7eb;
  --p-pending-t: #1f2937;
  --p-progress:  #3b82f6;
  --p-progress-t:#ffffff;
  --p-done:      #10b981;
  --p-done-t:    #ffffff;
  --p-overdue:   #ef4444;
  --p-overdue-t: #ffffff;
  /* blocked 状态本 spec 不做（Andon 模块加入时扩展） */

  --p-shift-early: #dbeafe;
  --p-shift-late:  #fef3c7;
  --p-shift-night: #e0e7ff;

  font-family: "Inter", "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 14px;
  background: var(--p-bg);
  color: var(--p-text);
}

.planner-toolbar { }
.planner-grid { }
.planner-cell { }
.planner-cell--drag-over { background: #dbeafe; }
.planner-card { }
.planner-card--in-progress { background: var(--p-progress); color: var(--p-progress-t); }
.planner-card--overdue { border: 2px solid var(--p-overdue); }
.planner-backlog { }
.planner-backlog__item { }
.planner-gantt { }
.planner-gantt__row { }
.planner-gantt__block { }
.planner-gantt__resize-handle { }
.planner-gantt__shift-band { }
```

### 5.2 加载方式

`base.html` 不动。Planner 模板 `{% block extra_head %}<link rel="stylesheet" href="/static/css/planner.css">{% endblock %}`。

### 5.3 全局 CSS 改动（最小）

- 引入 Inter font：self-host（"无 CDN"原则），下载 `inter.woff2` 到 `static/fonts/`，`@font-face` 在 planner.css 顶部声明
- 添加 `.container--planner` 类：宽度 100%、max 1600px、padding 0

### 5.4 班次 CRUD UI

`/production/shifts` 页面（admin/supervisor only）：
- 列表：code / name / time / days_of_week / line / active
- 表单：标准 LightMES 风格（**不**走 planner.css，保留现有 form 风格统一）
- 复用现有 `.card` `.data-table` 类

---

## 6. 服务层接口

### 6.1 `PlannerService`

```python
class PlannerService:
    def list_backlog(line_id: int | None = None) -> list[WorkOrder]
        """未排程工单（planned_start IS NULL OR line_id IS NULL）"""

    def list_scheduled_in_range(line_ids, start, end) -> list[WorkOrder]
        """时间范围内已排程工单"""

    def detect_conflict(line_id, start, end, exclude_wo_id) -> WorkOrder | None
        """返回冲突 WO（若有）"""

    def schedule(wo_id, line_id, start, end, user_id, force=False) -> WorkOrder
        """排程：写 planned_start/end/line_id + log change"""

    def unschedule(wo_id, user_id) -> WorkOrder
        """移除排程：清 planned_start/end"""

    def list_recent_changes(limit=50) -> list[ScheduleChangeLog]

    def undo_change(log_id, user_id) -> ScheduleChangeLog
        """undo + 写新 log"""
```

### 6.2 `ShiftService`

```python
class ShiftService:
    def create(data: ShiftCreate) -> Shift
    def update(shift_id, data: ShiftUpdate) -> Shift
    def delete(shift_id) -> None
    def list_all() -> list[Shift]
    def get_active_for_line(line_id) -> list[Shift]
    def current_at(line_id, now) -> Shift | None
```

---

## 7. 测试策略

| 测试 | 覆盖点 |
|---|---|
| `test_shift_create_validates_time_format` | HH:MM 格式 |
| `test_shift_create_rejects_invalid_days_of_week` | 元素 1-7 |
| `test_shift_cross_overnight_detection` | end < start 表示跨夜 |
| `test_planner_detect_conflict_overlap` | 同产线时间重叠 |
| `test_planner_detect_conflict_no_overlap` | 不重叠放行 |
| `test_planner_schedule_logs_change` | 写入 ScheduleChangeLog |
| `test_planner_schedule_blocks_on_conflict` | 冲突硬拦 |
| `test_planner_schedule_force_conflict_supervisor` | supervisor + force 跳过 |
| `test_planner_unschedule_returns_to_backlog` | 清 planned_start 后归 backlog |
| `test_planner_undo_restores_before_state` | undo 还原 |
| `test_planner_undo_blocks_if_before_conflict` | undo 时 before 窗口冲突则拒绝 |
| `test_planner_weekly_view_renders_grid` | 周视图渲染 |
| `test_planner_daily_view_renders_gantt` | 日视图渲染 |
| `test_planner_backlog_lists_unassigned` | backlog 列表 |
| `test_drag_drop_schedule_api` | HTMX POST 排程成功 |

---

## 8. 任务拆分（预估 9 task）

1. **Migration + Models**：shifts + schedule_change_logs + WorkOrder.priority
2. **ShiftService + Shift CRUD UI**（含测试）
3. **PlannerService 核心方法**（detect_conflict / schedule / unschedule + 测试）
4. **ScheduleChangeLog + undo**（+ 测试）
5. **Planner 周视图后端**（route + 渲染数据 + backlog）
6. **Planner 周视图前端**（HTML 网格 + HTML5 drag-drop + planner.css + Inter font）
7. **Planner 日视图**（Gantt + resize + snap）
8. **Planner 排程 API + 冲突检测**（HTMX 调用 + force_conflict 选项）
9. **Recent changes drawer + undo UI + 回归 + memory 更新**

---

## 9. 非目标 (Out of Scope)

- 月视图
- 多产线 WorkOrderPlacement
- 优先级规则引擎
- Capacity heatmap / OEE 集成
- Real-time polling（HTMX 轮询后续加）
- Maintenance 事件叠加
- CSV 导入工单
- 全局 UI 风格统一刷新（下个 spec）

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| HTML5 drag-drop 移动端体验差 | 车间主要是桌面/平板（含触屏键盘），先不做触屏优化；下次需要时加 SortableJS |
| 全局 UI 风格暂时不统一 | 接受短期不一致，下个 spec 统一刷新 |
| 排程变更日志表膨胀 | 暂不清理，未来加 90 天清理 cron |
| undo 冲突（before 时间窗已被占） | undo 时再校验冲突，冲突则拒绝并报错 |
| drag-drop 失败状态半成品 | service 层 atomic：先校验、再写、再 log，任一步失败全回滚 |
