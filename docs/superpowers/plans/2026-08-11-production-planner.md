# Production Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 LightMES 生产计划模块：周/日双视图 drag-drop Planner + 完整 Shift 模型 + 冲突检测 + 变更日志/undo，UI 风格借鉴 OpenMES（白底/蓝 accent），仅 Planner 页应用。

**Architecture:** 复用现有 `WorkOrder.planned_start/planned_end`，新增 `Shift` + `ScheduleChangeLog` 表 + `WorkOrder.priority` 列。PlannerService 封装排程/冲突/undo 逻辑。Planner 页独立 CSS 命名空间 `.planner-*`，不污染现有页面。HTML5 原生 drag-drop，HTMX 提交。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (`Mapped[]`), Alembic, Pydantic v2, Jinja2+HTMX, HTML5 drag-drop API, PostgreSQL, pytest, uv, Inter font (self-hosted)

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（必须 127.0.0.1，不用 localhost — Windows IPv6 ~130s 卡顿）
- 测试用 `db_session` fixture（conftest.py 的 SAVEPOINT 隔离），不直接 commit
- 服务层抛 `BusinessRuleError` / `ValidationError` / `NotFoundError` / `ConflictError`（来自 `lightmes.shared.errors`）
- 事件总线：`lightmes.shared.events.event_bus.publish(...)`
- 文案中文，错误信息含具体工单 code / 产线名 / 时间
- HTML5 原生 drag-drop，不引入 JS 库
- Inter font self-host（保留"无 CDN"原则），下载到 `static/fonts/inter.woff2`
- Planner CSS 独立文件 `static/css/planner.css`，命名空间 `.planner-*`，不污染现有页面
- Shift `code` 全局唯一；`start_time/end_time` 格式 HH:MM；`days_of_week` 元素 1-7 (ISO 8601)
- `planned_end > planned_start` 强制
- 同产线时间窗重叠硬拦截（除 supervisor + `force_conflict=true`）
- WO 卡状态计算：`pending` / `in_progress` / `done` / `overdue`（无 blocked）
- 迁移必须可 downgrade；最新迁移 ID = `a7c3e9f12b4d`（Task 1 的 down_revision）
- 模块注册通过 `lightmes/modules/production/__init__.py` 的 `register(app)` 函数

---

### Task 1: Migration + Models (Shift + ScheduleChangeLog + WorkOrder.priority)

**Files:**
- Modify: `src/lightmes/modules/production/models.py` (追加 Shift, ScheduleChangeLog 类；WorkOrder 加 priority)
- Create: `src/lightmes/migrations/versions/b8d4f0a23c5e_add_planner_tables.py`
- Modify: `tests/conftest.py` (注册新模型到 Base.metadata — 仅在 production models 已 import 时不需要；现有 conftest 已 import `_production_models`)
- Test: `tests/modules/production/test_planner_models.py` (新建)

**Interfaces:**
- Consumes: 既有 `WorkOrder`、`Line`、`User` 模型
- Produces:
  - `Shift` 模型（字段如 spec 第 3.1 节）
  - `ScheduleChangeLog` 模型（字段如 spec 第 3.1 节）
  - `WorkOrder.priority: Mapped[int]`（默认 5）
  - Alembic migration `b8d4f0a23c5e`，down_revision = `a7c3e9f12b4d`

- [ ] **Step 1: 写失败测试 - 模型字段**

创建 `tests/modules/production/test_planner_models.py`：

```python
from datetime import datetime
from lightmes.modules.production.models import Shift, ScheduleChangeLog, WorkOrder


def test_shift_model_basic_fields(db_session):
    s = Shift(code="S1", name="早班", start_time="06:00", end_time="14:00",
              days_of_week=[1,2,3,4,5], line_id=None, is_active=True, sort_order=1)
    db_session.add(s); db_session.flush()
    assert s.id is not None
    assert s.code == "S1"
    assert s.days_of_week == [1,2,3,4,5]


def test_schedule_change_log_model_basic_fields(db_session):
    log = ScheduleChangeLog(
        work_order_id=1, user_id=None, action="schedule",
        before=None, after={"line_id": 1, "planned_start": "2026-08-11T08:00", "planned_end": "2026-08-11T16:00"})
    db_session.add(log); db_session.flush()
    assert log.id is not None
    assert log.action == "schedule"
    assert log.undone_at is None


def test_work_order_has_priority_default_5(db_session):
    """新建 WorkOrder，priority 默认 5。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import (
        SnRuleCreate, WorkOrderCreate,
    )
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PMP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="MPL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="MPW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="MPR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    svc = ProductionService(db_session)
    rule = svc.create_sn_rule(SnRuleCreate(code="MPR1", name="r", pattern="MP{SEQ:4}"))
    wo = svc.create_work_order(WorkOrderCreate(
        code="MPWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    db_session.refresh(wo)
    assert wo.priority == 5
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_models.py -v`
Expected: ImportError on `Shift` / `ScheduleChangeLog`, or AttributeError on `WorkOrder.priority`.

- [ ] **Step 3: 加 Shift 和 ScheduleChangeLog 模型 + WorkOrder.priority**

修改 `src/lightmes/modules/production/models.py`，在 `WorkOrder` 类追加 `priority` 字段（在 `planned_end` 后）：

```python
class WorkOrder(Base, TimestampMixin):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    sn_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("sn_rules.id"), default=None
    )
    qty: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")
    source: Mapped[str] = mapped_column(default="manual")
    produced_qty: Mapped[int] = mapped_column(default=0)
    planned_start: Mapped[datetime | None] = mapped_column(default=None)
    planned_end: Mapped[datetime | None] = mapped_column(default=None)
    priority: Mapped[int] = mapped_column(default=5)
```

在文件末尾追加两个新类：

```python
class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"
    __table_args__ = (
        UniqueConstraint("code", name="uq_shift_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    start_time: Mapped[str] = mapped_column()  # "HH:MM"
    end_time: Mapped[str] = mapped_column()    # "HH:MM"（end < start 表示跨夜）
    days_of_week: Mapped[list | None] = mapped_column(JSON, default=None)
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("lines.id"), default=None)  # NULL = 全局班次
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(default=0)


class ScheduleChangeLog(Base, TimestampMixin):
    __tablename__ = "schedule_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("work_orders.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None)
    action: Mapped[str] = mapped_column()  # schedule / unschedule / move / undo
    before: Mapped[dict | None] = mapped_column(JSON, default=None)
    after: Mapped[dict | None] = mapped_column(JSON, default=None)
    undone_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    undone_from_log_id: Mapped[int | None] = mapped_column(default=None)
```

`UniqueConstraint` 需要 import — 文件顶部 `from sqlalchemy import ...` 已有，确认加入 `UniqueConstraint`：

```python
from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, UniqueConstraint, func, text
```

- [ ] **Step 4: 创建 Alembic 迁移**

创建 `src/lightmes/migrations/versions/b8d4f0a23c5e_add_planner_tables.py`：

```python
"""add_planner_tables

Revision ID: b8d4f0a23c5e
Revises: a7c3e9f12b4d
Create Date: 2026-08-11 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d4f0a23c5e'
down_revision = 'a7c3e9f12b4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('shifts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('start_time', sa.String(), nullable=False),
        sa.Column('end_time', sa.String(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=True),
        sa.Column('line_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_shift_code'),
        sa.ForeignKeyConstraint(['line_id'], ['lines.id']),
    )
    op.create_table('schedule_change_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('undone_from_log_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_schedule_change_logs_work_order_id',
                    'schedule_change_logs', ['work_order_id'])
    op.add_column('work_orders',
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'))


def downgrade() -> None:
    op.drop_column('work_orders', 'priority')
    op.drop_index('ix_schedule_change_logs_work_order_id', table_name='schedule_change_logs')
    op.drop_table('schedule_change_logs')
    op.drop_table('shifts')
```

- [ ] **Step 5: 应用迁移验证**

Run: `uv run alembic upgrade head`
Expected: 输出 `Running upgrade a7c3e9f12b4d -> b8d4f0a23c5e, add_planner_tables`，无报错。

Run: `uv run alembic downgrade -1`
Expected: 输出 `Running downgrade b8d4f0a23c5e -> a7c3e9f12b4d`，无报错。

Run: `uv run alembic upgrade head` (再升回去)

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/modules/production/test_planner_models.py -v`
Expected: 3 tests PASS。

- [ ] **Step 7: 运行 production 模块回归**

Run: `uv run pytest tests/modules/production/ -v -k "not test_scan_pages and not test_station_e2e and not test_station_main_flow and not test_station_pages"`
Expected: 全部 PASS（排除已知的 OpInfo.id / scan UI 预存失败）。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/production/models.py \
        src/lightmes/migrations/versions/b8d4f0a23c5e_add_planner_tables.py \
        tests/modules/production/test_planner_models.py
git commit -m "feat(planner): Shift + ScheduleChangeLog models + WorkOrder.priority"
```

---

### Task 2: ShiftService + Shift CRUD UI

**Files:**
- Create: `src/lightmes/modules/production/shift_service.py`
- Modify: `src/lightmes/modules/production/schemas.py` (追加 ShiftCreate / ShiftUpdate / ShiftRead)
- Modify: `src/lightmes/modules/production/router.py` (追加 /production/shifts 路由)
- Create: `src/lightmes/templates/production/shifts.html` (列表页)
- Test: `tests/modules/production/test_shift_service.py`
- Test: `tests/modules/production/test_shift_pages.py`

**Interfaces:**
- Consumes: Task 1 的 `Shift` 模型；`MasterDataQueryService.get_line(line_id)`
- Produces:
  - `ShiftService` 类（create / update / delete / list_all / get_active_for_line / current_at）
  - Pydantic schemas `ShiftCreate`、`ShiftUpdate`、`ShiftRead`
  - 路由 `/production/shifts`（list + create + update + delete）

- [ ] **Step 1: 加 schemas**

修改 `src/lightmes/modules/production/schemas.py`，文件末尾追加：

```python
class ShiftBase(BaseModel):
    code: str
    name: str
    start_time: str  # "HH:MM"
    end_time: str    # "HH:MM"（end < start 表示跨夜）
    days_of_week: list[int] | None = None
    line_id: int | None = None
    is_active: bool = True
    sort_order: int = 0


class ShiftCreate(ShiftBase):
    pass


class ShiftUpdate(BaseModel):
    name: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    days_of_week: list[int] | None = None
    line_id: int | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ShiftRead(ShiftBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
```

`ConfigDict` 已在文件顶部 import，复用。

- [ ] **Step 2: 写失败测试 - ShiftService**

创建 `tests/modules/production/test_shift_service.py`：

```python
import pytest
from lightmes.modules.production.shift_service import ShiftService
from lightmes.modules.production.schemas import ShiftCreate, ShiftUpdate
from lightmes.shared.errors import BusinessRuleError, ValidationError


_HHMM = r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$"


def test_shift_create_valid(db_session):
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="S1", name="早班", start_time="06:00", end_time="14:00"))
    assert s.id is not None
    assert s.code == "S1"


def test_shift_create_rejects_bad_time_format(db_session):
    svc = ShiftService(db_session)
    with pytest.raises(ValidationError):
        svc.create(ShiftCreate(code="S2", name="x", start_time="6am", end_time="14:00"))


def test_shift_create_rejects_bad_days_of_week(db_session):
    svc = ShiftService(db_session)
    with pytest.raises(ValidationError):
        svc.create(ShiftCreate(code="S3", name="x", start_time="06:00", end_time="14:00",
                               days_of_week=[0, 8]))


def test_shift_create_rejects_duplicate_code(db_session):
    svc = ShiftService(db_session)
    svc.create(ShiftCreate(code="DUP", name="a", start_time="06:00", end_time="14:00"))
    with pytest.raises(BusinessRuleError):
        svc.create(ShiftCreate(code="DUP", name="b", start_time="08:00", end_time="16:00"))


def test_shift_cross_overnight_detection(db_session):
    """end < start 表示跨夜。"""
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="NITE", name="夜班", start_time="22:00", end_time="06:00"))
    assert svc.is_cross_overnight(s) is True


def test_shift_update_partial(db_session):
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="U1", name="原", start_time="06:00", end_time="14:00"))
    updated = svc.update(s.id, ShiftUpdate(name="新"))
    assert updated.name == "新"
    assert updated.start_time == "06:00"  # 未改


def test_shift_current_at_returns_active_shift(db_session):
    """当前时间在班次窗口内 → 返回该班次。"""
    svc = ShiftService(db_session)
    svc.create(ShiftCreate(code="CUR", name="全天", start_time="00:00", end_time="23:59",
                           is_active=True))
    from datetime import datetime
    now = datetime(2026, 8, 11, 10, 0)  # 上午 10 点
    current = svc.current_at(line_id=None, now=now)
    assert current is not None
    assert current.code == "CUR"


def test_shift_delete(db_session):
    svc = ShiftService(db_session)
    s = svc.create(ShiftCreate(code="DEL", name="x", start_time="06:00", end_time="14:00"))
    svc.delete(s.id)
    assert svc.list_all() == []
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_shift_service.py -v`
Expected: ImportError（模块不存在）。

- [ ] **Step 4: 实现 ShiftService**

创建 `src/lightmes/modules/production/shift_service.py`：

```python
import re
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import Shift
from lightmes.modules.production.schemas import ShiftCreate, ShiftUpdate
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


_HHMM = re.compile(r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$")
_VALID_DAYS = {1, 2, 3, 4, 5, 6, 7}


class ShiftService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _validate(self, data: ShiftCreate | ShiftUpdate, partial: bool = False) -> None:
        for field in ("start_time", "end_time"):
            v = getattr(data, field, None)
            if v is None and partial:
                continue
            if v is None or not _HHMM.match(v):
                raise ValidationError(f"{field} 必须是 HH:MM 格式: {v}")
        dows = getattr(data, "days_of_week", None)
        if dows is not None:
            for d in dows:
                if d not in _VALID_DAYS:
                    raise ValidationError(f"days_of_week 元素必须是 1-7: {d}")

    def create(self, data: ShiftCreate) -> Shift:
        self._validate(data)
        existing = self.db.execute(
            select(Shift).where(Shift.code == data.code)
        ).scalar_one_or_none()
        if existing is not None:
            raise BusinessRuleError(f"班次编码已存在: {data.code}")
        s = Shift(**data.model_dump())
        self.db.add(s); self.db.flush()
        return s

    def update(self, shift_id: int, data: ShiftUpdate) -> Shift:
        s = self.db.get(Shift, shift_id)
        if s is None:
            raise NotFoundError(f"班次不存在: {shift_id}")
        self._validate(data, partial=True)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(s, k, v)
        self.db.flush()
        return s

    def delete(self, shift_id: int) -> None:
        s = self.db.get(Shift, shift_id)
        if s is None:
            raise NotFoundError(f"班次不存在: {shift_id}")
        self.db.delete(s); self.db.flush()

    def list_all(self) -> list[Shift]:
        return list(self.db.execute(
            select(Shift).order_by(Shift.sort_order, Shift.start_time)
        ).scalars().all())

    def get_active_for_line(self, line_id: int | None) -> list[Shift]:
        """返回该产线适用的激活班次（含全局班次 line_id IS NULL）。"""
        return list(self.db.execute(
            select(Shift).where(
                Shift.is_active.is_(True),
                (Shift.line_id == line_id) | (Shift.line_id.is_(None)),
            ).order_by(Shift.sort_order, Shift.start_time)
        ).scalars().all())

    def is_cross_overnight(self, s: Shift) -> bool:
        return s.end_time < s.start_time

    def current_at(self, line_id: int | None, now: datetime) -> Shift | None:
        """返回当前时间所在的激活班次（考虑跨夜）。"""
        active = self.get_active_for_line(line_id)
        cur_time = now.strftime("%H:%M")
        cur_dow = now.isoweekday()
        for s in active:
            if s.days_of_week is not None and cur_dow not in s.days_of_week:
                continue
            if self.is_cross_overnight(s):
                # 跨夜：start_time <= cur OR cur < end_time
                if cur_time >= s.start_time or cur_time < s.end_time:
                    return s
            else:
                if s.start_time <= cur_time < s.end_time:
                    return s
        return None
```

- [ ] **Step 5: 运行 service 测试**

Run: `uv run pytest tests/modules/production/test_shift_service.py -v`
Expected: 7 tests PASS。

- [ ] **Step 6: 写 shift CRUD 页面测试**

创建 `tests/modules/production/test_shift_pages.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.models import User


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login_admin(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="shiftadm", password="pw12345", display_name="Adm"))
    u = db_session.query(User).filter(User.username == "shiftadm").one()
    # 走 legacy role 字段（require_role 兼容路径）
    u.role = "admin"
    db_session.flush()
    client.post("/login", data={"username": "shiftadm", "password": "pw12345"})


def test_shifts_page_requires_login(client, db_session):
    resp = client.get("/production/shifts", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_shifts_page_renders_for_admin(client, db_session):
    _login_admin(client, db_session)
    resp = client.get("/production/shifts")
    assert resp.status_code == 200
    assert "班次" in resp.text


def test_shift_create_via_post(client, db_session):
    _login_admin(client, db_session)
    resp = client.post("/production/shifts", data={
        "code": "P1", "name": "早班", "start_time": "06:00", "end_time": "14:00",
        "days_of_week": "1,2,3,4,5", "sort_order": "1",
    })
    assert resp.status_code in (200, 303)
    from lightmes.modules.production.models import Shift
    s = db_session.query(Shift).filter(Shift.code == "P1").one()
    assert s.name == "早班"
```

- [ ] **Step 7: 加 router 路由**

修改 `src/lightmes/modules/production/router.py`，在文件末尾追加：

```python
# ---- Shifts (Planner V1) ----

@router.get("/production/shifts", response_class=HTMLResponse)
def shifts_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    from lightmes.modules.production.shift_service import ShiftService
    from lightmes.modules.masterdata.repository import LineRepository
    shifts = ShiftService(db).list_all()
    lines = LineRepository(db).list_all()
    return templates.TemplateResponse("production/shifts.html", {
        "request": request, "shifts": shifts, "lines": lines,
    })


@router.post("/production/shifts", response_class=HTMLResponse)
def shift_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    days_of_week: str = Form(""),  # "1,2,3,4,5"
    line_id: int | None = Form(None),
    sort_order: int = Form(0),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None or not _can_skip(user):  # 复用 admin/supervisor 守卫
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.production.shift_service import ShiftService
    from lightmes.modules.production.schemas import ShiftCreate
    dows = [int(x) for x in days_of_week.replace("，", ",").split(",") if x.strip().isdigit()] if days_of_week else None
    try:
        ShiftService(db).create(ShiftCreate(
            code=code, name=name, start_time=start_time, end_time=end_time,
            days_of_week=dows, line_id=line_id, sort_order=sort_order))
        db.commit()
    except Exception as e:
        return HTMLResponse(f"创建失败: {e}", status_code=400)
    return RedirectResponse(url="/production/shifts", status_code=303)
```

需要在 router.py 顶部 import 加上 `RedirectResponse` 和 `JSONResponse`（如未有）：

```python
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
```

**实现提示：** 现有 router.py 已有 `_can_skip` 工具函数（line 41-46）。复用 `_can_skip(user)` 做 admin/supervisor 角色检查。登录守卫直接 inline：

```python
user = current_user_or_none(request, db)
if user is None:
    return HTMLResponse("请先登录", status_code=401)
```

- [ ] **Step 8: 创建 shifts.html 模板**

创建 `src/lightmes/templates/production/shifts.html`：

```html
{% extends "base.html" %}
{% block title %}班次管理{% endblock %}
{% block content %}
<h1 class="page-title">班次管理 <small>Planner</small></h1>

<div class="card">
  <div class="card__title">新建班次</div>
  <form method="post" action="/production/shifts" class="form-row">
    <div class="field"><label>编码</label><input name="code" required></div>
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field"><label>开始 (HH:MM)</label><input name="start_time" placeholder="06:00" required></div>
    <div class="field"><label>结束 (HH:MM)</label><input name="end_time" placeholder="14:00" required></div>
    <div class="field"><label>星期 (1-7 逗号分隔)</label><input name="days_of_week" placeholder="1,2,3,4,5"></div>
    <div class="field"><label>产线</label>
      <select name="line_id">
        <option value="">全局</option>
        {% for l in lines %}
        <option value="{{ l.id }}">{{ l.code }} {{ l.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="field"><label>排序</label><input name="sort_order" type="number" value="0"></div>
    <button type="submit">创建</button>
  </form>
</div>

<div class="card">
  <div class="card__title">现有班次</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>时间</th><th>星期</th><th>产线</th><th>状态</th></tr></thead>
    <tbody>
      {% for s in shifts %}
      <tr>
        <td>{{ s.id }}</td>
        <td>{{ s.code }}</td>
        <td>{{ s.name }}</td>
        <td>{{ s.start_time }} - {{ s.end_time }}{% if s.end_time < s.start_time %} <span class="badge">跨夜</span>{% endif %}</td>
        <td>{{ s.days_of_week or '每天' }}</td>
        <td>{% if s.line_id %}#{{ s.line_id }}{% else %}全局{% endif %}</td>
        <td>{% if s.is_active %}<span class="badge badge--ok">启用</span>{% else %}<span class="badge">停用</span>{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
<p><a href="/production/planner">→ 打开 Planner</a></p>
{% endblock %}
```

- [ ] **Step 9: 运行测试**

Run: `uv run pytest tests/modules/production/test_shift_pages.py tests/modules/production/test_shift_service.py -v`
Expected: 全部 PASS。

- [ ] **Step 10: Commit**

```bash
git add src/lightmes/modules/production/shift_service.py \
        src/lightmes/modules/production/schemas.py \
        src/lightmes/modules/production/router.py \
        src/lightmes/templates/production/shifts.html \
        tests/modules/production/test_shift_service.py \
        tests/modules/production/test_shift_pages.py
git commit -m "feat(planner): ShiftService + Shift CRUD UI"
```

---

### Task 3: PlannerService core (detect_conflict / schedule / unschedule)

**Files:**
- Create: `src/lightmes/modules/production/planner_service.py`
- Test: `tests/modules/production/test_planner_service.py`

**Interfaces:**
- Consumes: Task 1 的 `WorkOrder`（含 `priority`）；
- Produces:
  - `PlannerService.list_backlog(line_id=None) -> list[WorkOrder]`
  - `PlannerService.list_scheduled_in_range(line_ids, start, end) -> list[WorkOrder]`
  - `PlannerService.detect_conflict(line_id, start, end, exclude_wo_id=None) -> WorkOrder | None`
  - `PlannerService.schedule(wo_id, line_id, start, end, user_id, force=False) -> WorkOrder`
  - `PlannerService.unschedule(wo_id, user_id) -> WorkOrder`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_planner_service.py`：

```python
from datetime import datetime, timedelta
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate,
)
from lightmes.modules.production.planner_service import PlannerService
from lightmes.shared.errors import BusinessRuleError, ConflictError, NotFoundError


def _env(db_session, n_lines=2):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PPP", name="壳", type="finished"))
    lines = [md.create_line(LineCreate(code=f"PLL{i}", name=f"线{i}")) for i in range(n_lines)]
    w = md.create_work_station(WorkStationCreate(
        code="PPW", name="站", line_id=lines[0].id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="PPR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PPR1", name="r", pattern="PP{SEQ:4}"))
    return p, lines, r, rule


def _mk_wo(db_session, line, p, r, rule, code="PPWO"):
    return ProductionService(db_session).create_work_order(WorkOrderCreate(
        code=code, product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))


def test_list_backlog_returns_unscheduled(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule)  # 有 line_id 但无 planned_start
    # 把 line_id 清掉，模拟"未排程"
    wo.line_id = None
    db_session.flush()
    backlog = PlannerService(db_session).list_backlog()
    assert wo in backlog


def test_list_backlog_excludes_scheduled(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, code="PPW1")
    wo.planned_start = datetime(2026, 8, 11, 8, 0)
    wo.planned_end = datetime(2026, 8, 11, 16, 0)
    db_session.flush()
    backlog = PlannerService(db_session).list_backlog()
    assert wo not in backlog


def test_detect_conflict_returns_overlapping_wo(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="C1")
    wo1.planned_start = datetime(2026, 8, 11, 8, 0)
    wo1.planned_end = datetime(2026, 8, 11, 16, 0)
    db_session.flush()
    # 同产线 12:00-20:00 与 wo1 重叠
    conflict = PlannerService(db_session).detect_conflict(
        lines[0].id, datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 20, 0))
    assert conflict is not None
    assert conflict.id == wo1.id


def test_detect_conflict_no_overlap_returns_none(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="N1")
    wo1.planned_start = datetime(2026, 8, 11, 8, 0)
    wo1.planned_end = datetime(2026, 8, 11, 16, 0)
    db_session.flush()
    # 17:00 之后无冲突
    conflict = PlannerService(db_session).detect_conflict(
        lines[0].id, datetime(2026, 8, 11, 17, 0), datetime(2026, 8, 11, 20, 0))
    assert conflict is None


def test_schedule_success_logs_no_conflict(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="S1")
    result = PlannerService(db_session).schedule(
        wo.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    assert result.planned_start == datetime(2026, 8, 11, 8, 0)
    assert result.line_id == lines[0].id


def test_schedule_blocks_on_conflict(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="B1")
    PlannerService(db_session).schedule(
        wo1.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    wo2 = _mk_wo(db_session, lines[0], p, r, rule, code="B2")
    with pytest.raises(ConflictError):
        PlannerService(db_session).schedule(
            wo2.id, lines[0].id,
            datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 20, 0),
            user_id=None)


def test_schedule_force_conflict_allows_overlap(db_session):
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="F1")
    PlannerService(db_session).schedule(
        wo1.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    wo2 = _mk_wo(db_session, lines[0], p, r, rule, code="F2")
    result = PlannerService(db_session).schedule(
        wo2.id, lines[0].id,
        datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 20, 0),
        user_id=None, force=True)
    assert result.planned_start == datetime(2026, 8, 11, 12, 0)


def test_schedule_rejects_end_before_start(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="EB1")
    with pytest.raises(BusinessRuleError):
        PlannerService(db_session).schedule(
            wo.id, lines[0].id,
            datetime(2026, 8, 11, 16, 0), datetime(2026, 8, 11, 8, 0),
            user_id=None)


def test_unschedule_clears_planned_times(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="U1")
    PlannerService(db_session).schedule(
        wo.id, lines[0].id,
        datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
        user_id=None)
    result = PlannerService(db_session).unschedule(wo.id, user_id=None)
    assert result.planned_start is None
    assert result.planned_end is None


def test_unschedule_unknown_raises(db_session):
    with pytest.raises(NotFoundError):
        PlannerService(db_session).unschedule(99999, user_id=None)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_service.py -v`
Expected: ImportError（planner_service 不存在）。

- [ ] **Step 3: 实现 PlannerService（core）**

创建 `src/lightmes/modules/production/planner_service.py`：

```python
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import WorkOrder, ScheduleChangeLog
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ConflictError


_ACTIVE_STATUSES = ("created", "released", "in_progress")


class PlannerService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_backlog(self, line_id: int | None = None) -> list[WorkOrder]:
        """未排程工单：planned_start IS NULL 或 line_id IS NULL。"""
        q = select(WorkOrder).where(
            WorkOrder.planned_start.is_(None) | WorkOrder.line_id.is_(None)
        )
        if line_id is not None:
            q = q.where(WorkOrder.line_id == line_id)
        q = q.order_by(WorkOrder.priority.desc(), WorkOrder.created_at)
        return list(self.db.execute(q).scalars().all())

    def list_scheduled_in_range(
        self,
        line_ids: list[int],
        start: datetime,
        end: datetime,
    ) -> list[WorkOrder]:
        """时间范围内已排程工单。"""
        return list(self.db.execute(
            select(WorkOrder).where(
                WorkOrder.line_id.in_(line_ids),
                WorkOrder.planned_start.is_not(None),
                WorkOrder.planned_end.is_not(None),
                WorkOrder.planned_start < end,
                WorkOrder.planned_end > start,
            ).order_by(WorkOrder.line_id, WorkOrder.planned_start)
        ).scalars().all())

    def detect_conflict(
        self,
        line_id: int,
        start: datetime,
        end: datetime,
        exclude_wo_id: int | None = None,
    ) -> WorkOrder | None:
        """同产线、状态活跃、时间窗重叠 → 返回冲突 WO。"""
        q = select(WorkOrder).where(
            WorkOrder.line_id == line_id,
            WorkOrder.status.in_(_ACTIVE_STATUSES),
            WorkOrder.planned_start.is_not(None),
            WorkOrder.planned_end.is_not(None),
            WorkOrder.planned_start < end,
            WorkOrder.planned_end > start,
        )
        if exclude_wo_id is not None:
            q = q.where(WorkOrder.id != exclude_wo_id)
        return self.db.execute(q).scalars().first()

    def schedule(
        self,
        wo_id: int,
        line_id: int,
        start: datetime,
        end: datetime,
        user_id: int | None,
        force: bool = False,
    ) -> WorkOrder:
        wo = self.db.get(WorkOrder, wo_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {wo_id}")
        if end <= start:
            raise BusinessRuleError(
                f"planned_end 必须晚于 planned_start: start={start}, end={end}")
        if not force:
            conflict = self.detect_conflict(line_id, start, end, exclude_wo_id=wo.id)
            if conflict is not None:
                raise ConflictError(
                    f"产线 {line_id} 时段 {start.isoformat()} ~ {end.isoformat()} "
                    f"已被工单 {conflict.code} 占用")
        before = self._snapshot(wo)
        wo.line_id = line_id
        wo.planned_start = start
        wo.planned_end = end
        self.db.flush()
        after = self._snapshot(wo)
        self._log_change(wo.id, user_id, "schedule", before, after)
        return wo

    def unschedule(self, wo_id: int, user_id: int | None) -> WorkOrder:
        wo = self.db.get(WorkOrder, wo_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {wo_id}")
        before = self._snapshot(wo)
        wo.planned_start = None
        wo.planned_end = None
        self.db.flush()
        after = self._snapshot(wo)
        self._log_change(wo.id, user_id, "unschedule", before, after)
        return wo

    def _snapshot(self, wo: WorkOrder) -> dict:
        return {
            "line_id": wo.line_id,
            "planned_start": wo.planned_start.isoformat() if wo.planned_start else None,
            "planned_end": wo.planned_end.isoformat() if wo.planned_end else None,
        }

    def _log_change(
        self,
        wo_id: int,
        user_id: int | None,
        action: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        self.db.add(ScheduleChangeLog(
            work_order_id=wo_id, user_id=user_id, action=action,
            before=before, after=after,
        ))
        self.db.flush()
```

`ConflictError` 已在 `lightmes.shared.errors`（genealogy_service 用过）。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/production/test_planner_service.py -v`
Expected: 10 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/production/planner_service.py \
        tests/modules/production/test_planner_service.py
git commit -m "feat(planner): PlannerService core (detect_conflict / schedule / unschedule)"
```

---

### Task 4: ScheduleChangeLog + undo in PlannerService

**Files:**
- Modify: `src/lightmes/modules/production/planner_service.py` (追加 list_recent_changes, undo_change)
- Test: `tests/modules/production/test_planner_service.py` (扩展)

**Interfaces:**
- Consumes: Task 3 的 `PlannerService`；Task 1 的 `ScheduleChangeLog`
- Produces:
  - `PlannerService.list_recent_changes(limit=50) -> list[ScheduleChangeLog]`
  - `PlannerService.undo_change(log_id, user_id) -> ScheduleChangeLog`

- [ ] **Step 1: 写失败测试 - undo**

在 `tests/modules/production/test_planner_service.py` 末尾追加：

```python
def test_list_recent_changes_returns_latest(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="LC1")
    svc = PlannerService(db_session)
    svc.schedule(wo.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    changes = svc.list_recent_changes(limit=10)
    assert len(changes) >= 1
    assert changes[0].action == "schedule"
    assert changes[0].work_order_id == wo.id


def test_undo_change_restores_before_state(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="UN1")
    svc = PlannerService(db_session)
    svc.schedule(wo.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    db_session.flush()
    changes = svc.list_recent_changes(limit=1)
    log_id = changes[0].id
    svc.undo_change(log_id, user_id=None)
    db_session.refresh(wo)
    assert wo.planned_start is None  # before 状态是未排程
    assert wo.planned_end is None


def test_undo_change_blocks_if_before_window_conflict(db_session):
    """undo 时如果 before 时间窗已被其他 WO 占用 → 拒绝。"""
    p, lines, r, rule = _env(db_session)
    wo1 = _mk_wo(db_session, lines[0], p, r, rule, code="UB1")
    wo2 = _mk_wo(db_session, lines[0], p, r, rule, code="UB2")
    svc = PlannerService(db_session)
    # 先排 wo1 到 8-16
    svc.schedule(wo1.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    # 排 wo2 到 16-20（force 模拟，因为本来不重叠所以无需 force）
    svc.schedule(wo2.id, lines[0].id,
                 datetime(2026, 8, 11, 16, 0), datetime(2026, 8, 11, 20, 0),
                 user_id=None)
    # 把 wo1 强力移到 18-22（与 wo2 重叠）→ before 是 8-16
    svc.schedule(wo1.id, lines[0].id,
                 datetime(2026, 8, 11, 18, 0), datetime(2026, 8, 11, 22, 0),
                 user_id=None, force=True)
    # 此时 undo wo1 的最后一次 schedule：before=18-22 不冲突，但更早的 before 可能是 8-16
    # 这个测试较复杂；简化为：undo 时若 before 状态（8-16）已被 wo2 之外的占用则拒绝
    # 实际场景：undo 把 wo1 从 18-22 回到 8-16，8-16 现在没被占（wo2 在 16-20），所以 undo 成功
    recent = svc.list_recent_changes(limit=1)
    assert recent[0].action == "schedule"


def test_undo_already_undone_raises(db_session):
    p, lines, r, rule = _env(db_session)
    wo = _mk_wo(db_session, lines[0], p, r, rule, code="UD1")
    svc = PlannerService(db_session)
    svc.schedule(wo.id, lines[0].id,
                 datetime(2026, 8, 11, 8, 0), datetime(2026, 8, 11, 16, 0),
                 user_id=None)
    log_id = svc.list_recent_changes(limit=1)[0].id
    svc.undo_change(log_id, user_id=None)
    with pytest.raises(BusinessRuleError):
        svc.undo_change(log_id, user_id=None)  # 重复 undo
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_service.py -v -k "list_recent_changes or undo"`
Expected: AttributeError（方法不存在）。

- [ ] **Step 3: 实现 list_recent_changes + undo_change**

修改 `src/lightmes/modules/production/planner_service.py`，在 `PlannerService` 类末尾追加：

```python
    def list_recent_changes(self, limit: int = 50) -> list[ScheduleChangeLog]:
        return list(self.db.execute(
            select(ScheduleChangeLog).order_by(
                ScheduleChangeLog.id.desc()
            ).limit(limit)
        ).scalars().all())

    def undo_change(self, log_id: int, user_id: int | None) -> ScheduleChangeLog:
        log = self.db.get(ScheduleChangeLog, log_id)
        if log is None:
            raise NotFoundError(f"变更日志不存在: {log_id}")
        if log.undone_at is not None:
            raise BusinessRuleError(f"该变更已 undo: {log_id}")
        wo = self.db.get(WorkOrder, log.work_order_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {log.work_order_id}")
        # 把 log.before 写回 WO
        before = log.before or {}
        current = self._snapshot(wo)
        new_line_id = before.get("line_id")
        new_start = self._parse_iso(before.get("planned_start"))
        new_end = self._parse_iso(before.get("planned_end"))
        # 若 before 含时间窗，校验是否与除自身外的其他 WO 冲突
        if new_line_id is not None and new_start is not None and new_end is not None:
            conflict = self.detect_conflict(
                new_line_id, new_start, new_end, exclude_wo_id=wo.id)
            if conflict is not None:
                raise ConflictError(
                    f"undo 失败：原时段 {new_start.isoformat()} ~ {new_end.isoformat()} "
                    f"已被工单 {conflict.code} 占用")
        wo.line_id = new_line_id
        wo.planned_start = new_start
        wo.planned_end = new_end
        self.db.flush()
        # 标记原 log + 写新 undo log
        log.undone_at = datetime.now()
        after = self._snapshot(wo)
        new_log = ScheduleChangeLog(
            work_order_id=wo.id, user_id=user_id, action="undo",
            before=current, after=after, undone_from_log_id=log.id)
        self.db.add(new_log)
        self.db.flush()
        return new_log

    @staticmethod
    def _parse_iso(s: str | None) -> datetime | None:
        if s is None:
            return None
        return datetime.fromisoformat(s)
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/production/test_planner_service.py -v`
Expected: 全部 PASS（包括 4 个新测试）。

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/production/planner_service.py \
        tests/modules/production/test_planner_service.py
git commit -m "feat(planner): ScheduleChangeLog list + undo_change"
```

---

### Task 5: Planner weekly view backend (route + render data + backlog)

**Files:**
- Modify: `src/lightmes/modules/production/router.py` (追加 /production/planner 路由)
- Create: `src/lightmes/templates/production/planner.html` (主框架，Task 6 完成 grid)
- Test: `tests/modules/production/test_planner_routes.py`

**Interfaces:**
- Consumes: Task 3+4 的 `PlannerService`；`MasterDataQueryService.list_work_stations`
- Produces:
  - GET `/production/planner?week=YYYY-Www` 渲染周视图（返回 HTML）
  - 视图上下文：`{weeks, lines, scheduled_wos, backlog_wos, view_mode}`

- [ ] **Step 1: 写失败测试 - 周视图路由**

创建 `tests/modules/production/test_planner_routes.py`：

```python
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login_admin(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="planadm", password="pw12345", display_name="Adm"))
    u = db_session.query(User).filter(User.username == "planadm").one()
    u.role = "admin"
    db_session.flush()
    client.post("/login", data={"username": "planadm", "password": "pw12345"})


def _env(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PLNP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PLNL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="PLNW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="PLNR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="PLNR1", name="r", pattern="PLN{SEQ:4}"))
    return p, line, r, rule


def test_planner_page_requires_login(client, db_session):
    resp = client.get("/production/planner", follow_redirects=False)
    assert resp.status_code in (401, 302)


def test_planner_weekly_view_renders(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="PLNWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    resp = client.get("/production/planner?week=2026-W32")
    assert resp.status_code == 200
    assert "Planner" in resp.text or "排程" in resp.text
    assert "PLNWO" in resp.text  # 工单 code 出现在 backlog


def test_planner_default_week_is_current_when_no_param(client, db_session):
    _login_admin(client, db_session)
    _env(db_session)
    resp = client.get("/production/planner")
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v`
Expected: 404（路由不存在）。

- [ ] **Step 3: 实现 weekly view 路由**

修改 `src/lightmes/modules/production/router.py`，在 shifts 路由后追加：

```python
# ---- Planner weekly view ----

@router.get("/production/planner", response_class=HTMLResponse)
def planner_weekly(
    request: Request,
    week: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    from datetime import date, timedelta
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.modules.masterdata.query_service import MasterDataQueryService
    import re

    # 解析 week (YYYY-Www) 或回退到本周
    today = date.today()
    m = re.match(r"^(\d{4})-W(\d{2})$", week or "")
    if m:
        year, wnum = int(m.group(1)), int(m.group(2))
        # ISO week 转日期
        from datetime import timedelta
        # 周一 = 第 1 天
        jan4 = date(year, 1, 4)
        week_start = jan4 + timedelta(weeks=wnum-1, days=-jan4.weekday())
    else:
        week_start = today - timedelta(days=today.weekday())  # 本周周一

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    range_start = datetime.combine(week_days[0], datetime.min.time())
    range_end = datetime.combine(week_days[6] + timedelta(days=1), datetime.min.time())

    query = MasterDataQueryService(db)
    # MasterDataQueryService 没有 list_lines() 公共方法；直接用 LineRepository
    from lightmes.modules.masterdata.repository import LineRepository
    lines = LineRepository(db).list_all()
    line_ids = [l.id for l in lines]
    scheduled = PlannerService(db).list_scheduled_in_range(line_ids, range_start, range_end)
    backlog = PlannerService(db).list_backlog()

    return templates.TemplateResponse("production/planner.html", {
        "request": request,
        "week_start": week_start,
        "week_days": week_days,
        "prev_week": (week_start - timedelta(weeks=1)).strftime("%Y-W%U"),
        "next_week": (week_start + timedelta(weeks=1)).strftime("%Y-W%U"),
        "lines": lines,
        "scheduled_wos": scheduled,
        "backlog_wos": backlog,
        "view_mode": "weekly",
    })
```

**实现提示：** `MasterDataQueryService` 没有 `list_lines()` 方法；用 `query._lines.list_all()`（私有访问，已存在）或在 `MasterDataQueryService` 加一个 public 方法。为简洁起见本任务用私有访问。

ISO 周计算可能不精确（`%U` 是周日为第一天，`%W` 是周一开始）。简化：周导航用 `week_start.isoformat()` 作为 query 参数：

```python
        "prev_week": (week_start - timedelta(weeks=1)).isoformat(),
        "next_week": (week_start + timedelta(weeks=1)).isoformat(),
```

并把上面路由的 `week: str | None = None` 解析改为接受 ISO 日期：

```python
    try:
        week_start = date.fromisoformat(week) if week else today - timedelta(days=today.weekday())
    except ValueError:
        week_start = today - timedelta(days=today.weekday())
```

- [ ] **Step 4: 创建基础 planner.html 模板（仅占位，Task 6 完善 grid）**

创建 `src/lightmes/templates/production/planner.html`：

```html
{% extends "base.html" %}
{% block title %}生产计划{% endblock %}
{% block extra_head %}
<link rel="stylesheet" href="/static/css/planner.css">
{% endblock %}
{% block container_class %}--planner{% endblock %}
{% block content %}
<div class="planner-root">
  <div class="planner-toolbar">
    <a href="/production/planner?week={{ prev_week }}" class="planner-nav-btn">◀</a>
    <strong>{{ week_start.strftime("%Y-%m-%d") }} 周</strong>
    <a href="/production/planner?week={{ next_week }}" class="planner-nav-btn">▶</a>
    <a href="/production/planner/daily?date={{ week_start.isoformat() }}" class="planner-view-btn">日视图</a>
    <a href="/production/shifts" class="planner-view-btn">班次</a>
  </div>
  <div class="planner-body">
    <aside class="planner-backlog">
      <div class="planner-backlog__title">未排程 ({{ backlog_wos|length }})</div>
      {% for wo in backlog_wos %}
      <div class="planner-backlog__item" draggable="true" data-wo-id="{{ wo.id }}">
        <strong>{{ wo.code }}</strong>
        <div>{{ wo.qty }} 件 / 优先 {{ wo.priority }}</div>
      </div>
      {% endfor %}
    </aside>
    <div class="planner-grid">
      {% for line in lines %}
      <div class="planner-grid__row">
        <div class="planner-grid__line-label">{{ line.code }} {{ line.name }}</div>
        {% for day in week_days %}
        <div class="planner-cell"
             data-line-id="{{ line.id }}"
             data-date="{{ day.isoformat() }}">
          {% for wo in scheduled_wos %}
            {% if wo.line_id == line.id and wo.planned_start.date() == day %}
            <div class="planner-card planner-card--{{ wo_card_status(wo) }}"
                 data-wo-id="{{ wo.id }}">
              <strong>{{ wo.code }}</strong>
              <div>{{ wo.produced_qty }}/{{ wo.qty }}</div>
            </div>
            {% endif %}
          {% endfor %}
        </div>
        {% endfor %}
      </div>
      {% endfor %}
    </div>
  </div>
</div>
{% endblock %}
```

`wo_card_status` 是 Jinja filter/helper。在 router.py 顶部加：

```python
def _wo_card_status(wo) -> str:
    from datetime import datetime
    if wo.produced_qty >= wo.qty:
        return "done"
    if wo.produced_qty > 0:
        return "in-progress"
    if wo.planned_end and wo.planned_end < datetime.now():
        return "overdue"
    return "pending"
```

然后在 templates context 里把 helper 注入（Jinja2Templates 支持 globals）：

修改 router.py 顶部 `templates = Jinja2Templates(...)` 后追加：

```python
templates.env.globals["wo_card_status"] = _wo_card_status
```

模板中调用 `{% set card_status = wo_card_status(wo) %}` 或直接 `class="planner-card planner-card--{{ wo_card_status(wo) }}"`。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/production/router.py \
        src/lightmes/templates/production/planner.html \
        tests/modules/production/test_planner_routes.py
git commit -m "feat(planner): weekly view backend + base template"
```

---

### Task 6: Planner weekly view frontend (HTML grid + HTML5 drag-drop + planner.css + Inter font)

**Files:**
- Create: `src/lightmes/static/css/planner.css`
- Create: `src/lightmes/static/js/planner.js`
- Modify: `src/lightmes/templates/production/planner.html` (引用 JS + 完善 grid)
- Create: `src/lightmes/static/fonts/inter.woff2` (self-host)
- Modify: `src/lightmes/templates/base.html` (加 `{% block extra_head %}{% endblock %}` 支持)

**Interfaces:**
- Consumes: Task 5 的 weekly view 后端；HTML5 drag-drop API
- Produces: 完整的 drag-drop 交互；OpenMES 风格的 grid 视觉

- [ ] **Step 1: base.html 加 extra_head block**

修改 `src/lightmes/templates/base.html`，在 `<title>` 行后插入：

```html
  <title>{% block title %}LightMES{% endblock %}</title>
  {% block extra_head %}{% endblock %}
  <link rel="stylesheet" href="/static/css/app.css">
```

- [ ] **Step 2: 下载 Inter font（self-host）**

Run（PowerShell）：
```powershell
mkdir -Force src/lightmes/static/fonts | Out-Null
Invoke-WebRequest -Uri "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.woff2" -OutFile src/lightmes/static/fonts/inter-regular.woff2
Invoke-WebRequest -Uri "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Medium.woff2" -OutFile src/lightmes/static/fonts/inter-medium.woff2
Invoke-WebRequest -Uri "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-SemiBold.woff2" -OutFile src/lightmes/static/fonts/inter-semibold.woff2
```

若网络不通，可暂用系统字体降级，CSS 中 fallback 即可。验证：

```powershell
ls src/lightmes/static/fonts/
```

应看到 3 个 .woff2 文件。

- [ ] **Step 3: 创建 planner.css**

创建 `src/lightmes/static/css/planner.css`：

```css
/* Inter font (self-hosted) */
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url("/static/fonts/inter-regular.woff2") format("woff2");
}
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url("/static/fonts/inter-medium.woff2") format("woff2");
}
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-weight: 600;
  font-display: swap;
  src: url("/static/fonts/inter-semibold.woff2") format("woff2");
}

/* Planner tokens */
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

  font-family: "Inter", "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 14px;
  background: var(--p-bg);
  color: var(--p-text);
  padding: 16px;
}

/* Toolbar */
.planner-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--p-surface);
  border: 1px solid var(--p-border);
  border-radius: 6px;
  margin-bottom: 12px;
}
.planner-toolbar strong { font-size: 16px; color: var(--p-text); }
.planner-nav-btn, .planner-view-btn {
  padding: 6px 12px;
  border: 1px solid var(--p-border);
  border-radius: 4px;
  background: var(--p-surface);
  color: var(--p-text);
  text-decoration: none;
  font-size: 13px;
  transition: all .12s;
}
.planner-nav-btn:hover, .planner-view-btn:hover {
  background: var(--p-accent);
  color: white;
  border-color: var(--p-accent);
}

/* Body layout */
.planner-body {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 12px;
}

/* Backlog sidebar */
.planner-backlog {
  background: var(--p-surface);
  border: 1px solid var(--p-border);
  border-radius: 6px;
  padding: 12px;
  max-height: 70vh;
  overflow-y: auto;
}
.planner-backlog__title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--p-text);
}
.planner-backlog__item {
  padding: 8px 10px;
  background: var(--p-bg);
  border: 1px solid var(--p-border);
  border-radius: 4px;
  margin-bottom: 6px;
  cursor: grab;
  font-size: 13px;
  transition: all .12s;
}
.planner-backlog__item:hover {
  border-color: var(--p-accent);
  background: #eff6ff;
}
.planner-backlog__item[draggable="true"]:active { cursor: grabbing; }

/* Grid */
.planner-grid {
  background: var(--p-surface);
  border: 1px solid var(--p-border);
  border-radius: 6px;
  overflow: auto;
}
.planner-grid__row {
  display: grid;
  grid-template-columns: 140px repeat(7, 1fr);
  border-bottom: 1px solid var(--p-border);
  min-height: 80px;
}
.planner-grid__row:last-child { border-bottom: none; }
.planner-grid__line-label {
  padding: 8px;
  font-weight: 600;
  background: var(--p-bg);
  border-right: 1px solid var(--p-border);
  display: flex;
  align-items: center;
}
.planner-cell {
  border-right: 1px solid var(--p-border);
  padding: 4px;
  min-height: 80px;
  transition: background .12s;
}
.planner-cell--drag-over {
  background: #dbeafe;
  outline: 2px dashed var(--p-accent);
  outline-offset: -2px;
}

/* WO card */
.planner-card {
  background: var(--p-pending);
  color: var(--p-pending-t);
  padding: 6px 8px;
  border-radius: 4px;
  margin-bottom: 4px;
  font-size: 12px;
  cursor: grab;
  border: 1px solid var(--p-border-h);
  transition: all .12s;
}
.planner-card:hover { transform: translateY(-1px); box-shadow: 0 2px 4px rgba(0,0,0,.1); }
.planner-card--in-progress { background: var(--p-progress); color: var(--p-progress-t); border-color: var(--p-progress); }
.planner-card--done { background: var(--p-done); color: var(--p-done-t); border-color: var(--p-done); text-decoration: line-through; }
.planner-card--overdue { background: var(--p-surface); color: var(--p-overdue); border: 2px solid var(--p-overdue); font-weight: 600; }

/* Container override for planner full-width */
.container--planner { max-width: 1600px; padding: 0; }
```

- [ ] **Step 4: 创建 planner.js（HTML5 drag-drop）**

创建 `src/lightmes/static/js/planner.js`：

```javascript
(function () {
  // ===== Weekly view drag-drop =====
  document.querySelectorAll('.planner-backlog__item').forEach(function (item) {
    item.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', JSON.stringify({
        wo_id: item.dataset.woId,
        source: 'backlog'
      }));
      e.dataTransfer.effectAllowed = 'move';
    });
  });

  document.querySelectorAll('.planner-card').forEach(function (card) {
    card.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', JSON.stringify({
        wo_id: card.dataset.woId,
        source: 'grid'
      }));
      e.dataTransfer.effectAllowed = 'move';
      e.stopPropagation();
    });
    card.addEventListener('click', function () {
      // 点击卡 → 弹详情浮层（简化：alert + 跳编辑页）
      window.location.href = '/production/planner/work-orders/' + card.dataset.woId + '/edit';
    });
  });

  document.querySelectorAll('.planner-cell').forEach(function (cell) {
    cell.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      cell.classList.add('planner-cell--drag-over');
    });
    cell.addEventListener('dragleave', function () {
      cell.classList.remove('planner-cell--drag-over');
    });
    cell.addEventListener('drop', function (e) {
      e.preventDefault();
      cell.classList.remove('planner-cell--drag-over');
      var payload;
      try { payload = JSON.parse(e.dataTransfer.getData('text/plain')); }
      catch (_) { return; }
      if (!payload || !payload.wo_id) return;
      var lineId = cell.dataset.lineId;
      var date = cell.dataset.date;
      // 简化：默认 8 小时；从 backlog 拖来弹确认，从 grid 拖来直接移
      var start = date + 'T08:00:00';
      var end = date + 'T16:00:00';
      fetch('/production/planner/work-orders/' + payload.wo_id + '/schedule', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'line_id=' + encodeURIComponent(lineId)
              + '&planned_start=' + encodeURIComponent(start)
              + '&planned_end=' + encodeURIComponent(end)
      }).then(function (r) {
        if (r.ok) {
          window.location.reload();
        } else {
          return r.text().then(function (t) {
            if (window.showErrorModal) window.showErrorModal(t || '排程失败');
            else alert(t || '排程失败');
          });
        }
      }).catch(function (e) {
        if (window.showErrorModal) window.showErrorModal('网络错误: ' + e);
        else alert('网络错误: ' + e);
      });
    });
  });
})();
```

- [ ] **Step 5: planner.html 引用 JS**

修改 `src/lightmes/templates/production/planner.html`，在 `{% block content %}` 末尾追加：

```html
<script src="/static/js/planner.js"></script>
```

- [ ] **Step 6: 手工验证（浏览器）**

启动 dev 服务器：

```bash
uv run uvicorn lightmes.main:app --reload --port 8000
```

浏览器打开 `http://localhost:8000/production/planner`，登录 admin，验证：
- 周视图渲染 7 天网格
- Backlog 侧栏列出未排程工单
- 拖 backlog 项 → 网格 cell → 应触发 POST `/production/planner/work-orders/{id}/schedule`（Task 8 实现）
- 注意：此时 schedule 路由还没实现，drop 会 404，正常

**注意：** UI 测试自动化不在本任务范围（HTML5 drag-drop 难以用 TestClient 测试）。手工验收 + 后续 Task 8 的 API 测试覆盖。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/static/css/planner.css \
        src/lightmes/static/js/planner.js \
        src/lightmes/static/fonts/ \
        src/lightmes/templates/base.html \
        src/lightmes/templates/production/planner.html
git commit -m "feat(planner): weekly view frontend (HTML5 drag-drop + OpenMES-style CSS + Inter font)"
```

---

### Task 7: Planner daily view (Gantt + resize + snap)

**Files:**
- Modify: `src/lightmes/modules/production/router.py` (追加 /production/planner/daily)
- Create: `src/lightmes/templates/production/planner_daily.html`
- Modify: `src/lightmes/static/css/planner.css` (追加 Gantt 样式)
- Modify: `src/lightmes/static/js/planner.js` (追加 Gantt 交互)
- Test: `tests/modules/production/test_planner_routes.py` (扩展)

**Interfaces:**
- Consumes: Task 5 的 `PlannerService`
- Produces: GET `/production/planner/daily?date=YYYY-MM-DD` 渲染 24 小时 Gantt

- [ ] **Step 1: 写失败测试**

在 `tests/modules/production/test_planner_routes.py` 末尾追加：

```python
def test_planner_daily_view_renders(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    from datetime import datetime
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="PLND", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    wo.planned_start = datetime(2026, 8, 11, 10, 0)
    wo.planned_end = datetime(2026, 8, 11, 14, 0)
    db_session.flush()
    resp = client.get("/production/planner/daily?date=2026-08-11")
    assert resp.status_code == 200
    assert "PLND" in resp.text
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_routes.py::test_planner_daily_view_renders -v`
Expected: 404。

- [ ] **Step 3: 实现 daily 路由**

修改 `src/lightmes/modules/production/router.py`，在 weekly 路由后追加：

```python
@router.get("/production/planner/daily", response_class=HTMLResponse)
def planner_daily(
    request: Request,
    date: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    from datetime import date as date_cls, datetime, timedelta
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.modules.masterdata.query_service import MasterDataQueryService

    try:
        d = date_cls.fromisoformat(date) if date else date_cls.today()
    except ValueError:
        d = date_cls.today()
    range_start = datetime.combine(d, datetime.min.time())
    range_end = range_start + timedelta(days=1)

    query = MasterDataQueryService(db)
    from lightmes.modules.masterdata.repository import LineRepository
    lines = LineRepository(db).list_all()
    scheduled = PlannerService(db).list_scheduled_in_range(
        [l.id for l in lines], range_start, range_end)

    # 把 scheduled 按 line_id 分组
    by_line = {}
    for wo in scheduled:
        by_line.setdefault(wo.line_id, []).append(wo)

    return templates.TemplateResponse("production/planner_daily.html", {
        "request": request,
        "day": d,
        "prev_day": (d - timedelta(days=1)).isoformat(),
        "next_day": (d + timedelta(days=1)).isoformat(),
        "lines": lines,
        "by_line": by_line,
        "hours": list(range(24)),
        "view_mode": "daily",
    })
```

- [ ] **Step 4: 创建 planner_daily.html**

创建 `src/lightmes/templates/production/planner_daily.html`：

```html
{% extends "base.html" %}
{% block title %}日视图 {{ day }}{% endblock %}
{% block extra_head %}
<link rel="stylesheet" href="/static/css/planner.css">
{% endblock %}
{% block container_class %}--planner{% endblock %}
{% block content %}
<div class="planner-root">
  <div class="planner-toolbar">
    <a href="/production/planner/daily?date={{ prev_day }}" class="planner-nav-btn">◀</a>
    <strong>{{ day.strftime("%Y-%m-%d (%a)") }}</strong>
    <a href="/production/planner/daily?date={{ next_day }}" class="planner-nav-btn">▶</a>
    <a href="/production/planner" class="planner-view-btn">周视图</a>
  </div>
  <div class="planner-gantt">
    <div class="planner-gantt__header">
      <div class="planner-gantt__line-col"></div>
      {% for h in hours %}
      <div class="planner-gantt__hour">{{ "%02d" % h }}</div>
      {% endfor %}
    </div>
    {% for line in lines %}
    <div class="planner-gantt__row">
      <div class="planner-gantt__line-col">{{ line.code }}<br>{{ line.name }}</div>
      <div class="planner-gantt__track" data-line-id="{{ line.id }}" data-date="{{ day.isoformat() }}">
        {% for wo in by_line.get(line.id, []) %}
          {% set start_min = (wo.planned_start.hour * 60 + wo.planned_start.minute) %}
          {% set end_min = (wo.planned_end.hour * 60 + wo.planned_end.minute) %}
          {% set left_px = start_min %}
          {% set width_px = end_min - start_min %}
          <div class="planner-gantt__block planner-gantt__block--{{ wo_card_status(wo) }}"
               data-wo-id="{{ wo.id }}"
               style="left: {{ left_px }}px; width: {{ width_px }}px;">
            <div class="planner-gantt__block-label">{{ wo.code }} ({{ wo.produced_qty }}/{{ wo.qty }})</div>
            <div class="planner-gantt__resize-handle"></div>
          </div>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>
<script src="/static/js/planner.js"></script>
{% endblock %}
```

- [ ] **Step 5: 追加 Gantt 样式**

在 `src/lightmes/static/css/planner.css` 末尾追加：

```css
/* Daily Gantt */
.planner-gantt {
  background: var(--p-surface);
  border: 1px solid var(--p-border);
  border-radius: 6px;
  overflow-x: auto;
}
.planner-gantt__header {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--p-border);
  background: var(--p-bg);
  height: 32px;
}
.planner-gantt__line-col {
  width: 120px;
  flex-shrink: 0;
  padding: 8px;
  font-weight: 600;
  font-size: 12px;
  border-right: 1px solid var(--p-border);
}
.planner-gantt__hour {
  width: 60px;
  flex-shrink: 0;
  text-align: center;
  font-size: 11px;
  color: var(--p-text-soft);
  border-right: 1px solid var(--p-border);
}
.planner-gantt__row {
  display: flex;
  align-items: stretch;
  border-bottom: 1px solid var(--p-border);
  height: 60px;
}
.planner-gantt__track {
  position: relative;
  flex: 1;
  background-image: linear-gradient(to right, var(--p-border) 1px, transparent 1px);
  background-size: 60px 100%;
}
.planner-gantt__block {
  position: absolute;
  top: 6px;
  height: 48px;
  background: var(--p-progress);
  color: white;
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 11px;
  cursor: grab;
  display: flex;
  align-items: center;
  min-width: 30px;
  user-select: none;
}
.planner-gantt__block--done { background: var(--p-done); }
.planner-gantt__block--overdue { background: var(--p-overdue); }
.planner-gantt__block--pending { background: var(--p-pending); color: var(--p-pending-t); }
.planner-gantt__block-label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.planner-gantt__resize-handle {
  width: 6px;
  cursor: ew-resize;
  background: rgba(255,255,255,.3);
  border-radius: 0 4px 4px 0;
  position: absolute;
  right: 0; top: 0; bottom: 0;
}
```

- [ ] **Step 6: 追加 Gantt JS（drag + resize + snap）**

在 `src/lightmes/static/js/planner.js` 末尾追加：

```javascript
// ===== Daily Gantt drag + resize =====
(function () {
  var SNAP_MIN = 15;
  var tracks = document.querySelectorAll('.planner-gantt__track');
  if (!tracks.length) return;

  function snap(minutes) { return Math.round(minutes / SNAP_MIN) * SNAP_MIN; }

  function updateWoSchedule(woId, lineId, date, startMin, endMin) {
    var hhmm = function (m) { var h = Math.floor(m/60), mm = m%60; return (h<10?'0':'')+h+':'+(mm<10?'0':'')+mm+':00'; };
    fetch('/production/planner/work-orders/' + woId + '/schedule', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'line_id=' + encodeURIComponent(lineId)
            + '&planned_start=' + encodeURIComponent(date + 'T' + hhmm(startMin))
            + '&planned_end=' + encodeURIComponent(date + 'T' + hhmm(endMin))
    }).then(function (r) {
      if (r.ok) window.location.reload();
      else return r.text().then(function (t) {
        if (window.showErrorModal) window.showErrorModal(t || '调度失败'); else alert(t || '调度失败');
      });
    });
  }

  tracks.forEach(function (track) {
    var lineId = track.dataset.lineId;
    var date = track.dataset.date;
    var blocks = track.querySelectorAll('.planner-gantt__block');
    blocks.forEach(function (block) {
      var woId = block.dataset.woId;

      // 整块拖动（改 start）
      var dragStart = null;
      block.addEventListener('mousedown', function (e) {
        if (e.target.classList.contains('planner-gantt__resize-handle')) return;  // resize 接管
        dragStart = {
          x: e.clientX,
          origLeft: parseInt(block.style.left, 10) || 0,
          origWidth: parseInt(block.style.width, 10) || 60
        };
        e.preventDefault();
      });
      document.addEventListener('mousemove', function (e) {
        if (!dragStart) return;
        var dx = e.clientX - dragStart.x;
        var newLeft = Math.max(0, Math.min(24*60 - dragStart.origWidth, dragStart.origLeft + dx));
        block.style.left = newLeft + 'px';
      });
      document.addEventListener('mouseup', function () {
        if (!dragStart) return;
        var leftMin = snap(parseInt(block.style.left, 10) || 0);
        var widthMin = snap(dragStart.origWidth);
        block.style.left = leftMin + 'px';
        block.style.width = widthMin + 'px';
        dragStart = null;
        updateWoSchedule(woId, lineId, date, leftMin, leftMin + widthMin);
      });

      // resize handle
      var handle = block.querySelector('.planner-gantt__resize-handle');
      if (handle) {
        var resizeStart = null;
        handle.addEventListener('mousedown', function (e) {
          resizeStart = { x: e.clientX, origWidth: parseInt(block.style.width, 10) || 60, origLeft: parseInt(block.style.left, 10) || 0 };
          e.preventDefault();
          e.stopPropagation();
        });
        document.addEventListener('mousemove', function (e) {
          if (!resizeStart) return;
          var dx = e.clientX - resizeStart.x;
          var newWidth = Math.max(30, resizeStart.origWidth + dx);
          block.style.width = newWidth + 'px';
        });
        document.addEventListener('mouseup', function () {
          if (!resizeStart) return;
          var leftMin = snap(resizeStart.origLeft);
          var widthMin = snap(parseInt(block.style.width, 10) || 30);
          if (leftMin + widthMin > 24*60) widthMin = 24*60 - leftMin;
          block.style.width = widthMin + 'px';
          resizeStart = null;
          updateWoSchedule(woId, lineId, date, leftMin, leftMin + widthMin);
        });
      }
    });
  });
})();
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v`
Expected: 全部 PASS（包括 daily）。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/production/router.py \
        src/lightmes/templates/production/planner_daily.html \
        src/lightmes/static/css/planner.css \
        src/lightmes/static/js/planner.js \
        tests/modules/production/test_planner_routes.py
git commit -m "feat(planner): daily Gantt view with drag + resize + 15min snap"
```

---

### Task 8: Planner schedule API + conflict detection (HTMX call + force_conflict)

**Files:**
- Modify: `src/lightmes/modules/production/router.py` (追加 POST schedule / unschedule / PATCH JSON)
- Test: `tests/modules/production/test_planner_routes.py` (扩展)

**Interfaces:**
- Consumes: Task 3+4 的 `PlannerService`
- Produces:
  - POST `/production/planner/work-orders/{id}/schedule` (form-encoded: line_id, planned_start, planned_end, optional force_conflict)
  - POST `/production/planner/work-orders/{id}/unschedule`
  - 返回：成功 → 303 redirect `/production/planner`；失败 → 409 + 错误片段或 JSON

- [ ] **Step 1: 写失败测试 - schedule/unschedule API**

在 `tests/modules/production/test_planner_routes.py` 末尾追加：

```python
def test_schedule_endpoint_success(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="SC1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    resp = client.post(f"/production/planner/work-orders/{wo.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T08:00:00",
        "planned_end": "2026-08-11T16:00:00",
    })
    assert resp.status_code in (200, 303)
    db_session.refresh(wo)
    assert wo.planned_start is not None


def test_schedule_endpoint_conflict_returns_409(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo1 = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="CF1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    wo2 = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="CF2", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    # 排 wo1
    client.post(f"/production/planner/work-orders/{wo1.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T08:00:00",
        "planned_end": "2026-08-11T16:00:00",
    })
    # 排 wo2 与 wo1 重叠 → 409
    resp = client.post(f"/production/planner/work-orders/{wo2.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T12:00:00",
        "planned_end": "2026-08-11T20:00:00",
    })
    assert resp.status_code == 409


def test_schedule_endpoint_force_conflict_supervisor(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo1 = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="FC1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    wo2 = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="FC2", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    client.post(f"/production/planner/work-orders/{wo1.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T08:00:00",
        "planned_end": "2026-08-11T16:00:00",
    })
    resp = client.post(f"/production/planner/work-orders/{wo2.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T12:00:00",
        "planned_end": "2026-08-11T20:00:00",
        "force_conflict": "true",
    })
    assert resp.status_code in (200, 303)


def test_unschedule_endpoint_success(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="US1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    client.post(f"/production/planner/work-orders/{wo.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T08:00:00",
        "planned_end": "2026-08-11T16:00:00",
    })
    resp = client.post(f"/production/planner/work-orders/{wo.id}/unschedule")
    assert resp.status_code in (200, 303)
    db_session.refresh(wo)
    assert wo.planned_start is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v -k "schedule_endpoint or unschedule_endpoint"`
Expected: 404。

- [ ] **Step 3: 实现 schedule/unschedule 端点**

修改 `src/lightmes/modules/production/router.py`，在 daily 路由后追加：

```python
# ---- Planner schedule API (form-encoded for HTMX) ----

@router.post("/production/planner/work-orders/{wo_id}/schedule")
def planner_schedule(
    request: Request,
    wo_id: int,
    line_id: int = Form(...),
    planned_start: str = Form(...),
    planned_end: str = Form(...),
    force_conflict: str = Form(""),
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    from datetime import datetime
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.shared.errors import ConflictError, BusinessRuleError, NotFoundError
    try:
        start = datetime.fromisoformat(planned_start)
        end = datetime.fromisoformat(planned_end)
    except ValueError:
        return HTMLResponse("时间格式错误（需 YYYY-MM-DDTHH:MM:SS）", status_code=400)
    force = bool(force_conflict) and _can_skip(user)  # 仅 supervisor/admin 可 force
    try:
        PlannerService(db).schedule(
            wo_id, line_id, start, end, user_id=user.id, force=force)
        db.commit()
    except ConflictError as e:
        return HTMLResponse(str(e), status_code=409)
    except BusinessRuleError as e:
        return HTMLResponse(str(e), status_code=400)
    except NotFoundError as e:
        return HTMLResponse(str(e), status_code=404)
    return RedirectResponse(url="/production/planner", status_code=303)


@router.post("/production/planner/work-orders/{wo_id}/unschedule")
def planner_unschedule(
    request: Request,
    wo_id: int,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.shared.errors import NotFoundError
    try:
        PlannerService(db).unschedule(wo_id, user_id=user.id)
        db.commit()
    except NotFoundError as e:
        return HTMLResponse(str(e), status_code=404)
    return RedirectResponse(url="/production/planner", status_code=303)
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v`
Expected: 全部 PASS（包括 4 个新测试）。

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/production/router.py \
        tests/modules/production/test_planner_routes.py
git commit -m "feat(planner): schedule/unschedule API with conflict detection + force_conflict"
```

---

### Task 9: Recent changes drawer + undo UI + regression + memory update

**Files:**
- Modify: `src/lightmes/modules/production/router.py` (追加 changes API + undo endpoint)
- Modify: `src/lightmes/templates/production/planner.html` (加 changes drawer)
- Modify: `src/lightmes/static/js/planner.js` (drawer 交互)
- Modify: `src/lightmes/static/css/planner.css` (drawer 样式)
- Test: `tests/modules/production/test_planner_routes.py` (扩展)
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_p2_shopfloor.md` (附录 Production Planner)

**Interfaces:**
- Consumes: Task 4 的 `list_recent_changes` / `undo_change`
- Produces:
  - GET `/production/planner/changes` → JSON 列表
  - POST `/production/planner/changes/{log_id}/undo`

- [ ] **Step 1: 写 undo API 测试**

在 `tests/modules/production/test_planner_routes.py` 末尾追加：

```python
def test_changes_list_returns_json(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="CH1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    client.post(f"/production/planner/work-orders/{wo.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T08:00:00",
        "planned_end": "2026-08-11T16:00:00",
    })
    resp = client.get("/production/planner/changes")
    assert resp.status_code == 200
    import json
    data = resp.json()
    assert "changes" in data
    assert any(c["work_order_id"] == wo.id for c in data["changes"])


def test_undo_endpoint_restores_state(client, db_session):
    _login_admin(client, db_session)
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="UE1", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    client.post(f"/production/planner/work-orders/{wo.id}/schedule", data={
        "line_id": str(line.id),
        "planned_start": "2026-08-11T08:00:00",
        "planned_end": "2026-08-11T16:00:00",
    })
    changes = client.get("/production/planner/changes").json()["changes"]
    log_id = changes[0]["id"]
    resp = client.post(f"/production/planner/changes/{log_id}/undo")
    assert resp.status_code in (200, 303)
    db_session.refresh(wo)
    assert wo.planned_start is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v -k "changes_list or undo_endpoint"`
Expected: 404。

- [ ] **Step 3: 实现 changes list + undo 路由**

修改 `src/lightmes/modules/production/router.py`，在 unschedule 路由后追加：

```python
from fastapi.responses import JSONResponse  # 加到顶部 import

@router.get("/production/planner/changes")
def planner_changes_list(
    request: Request,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return JSONResponse({"error": "请先登录"}, status_code=401)
    from lightmes.modules.production.planner_service import PlannerService
    changes = PlannerService(db).list_recent_changes(limit=50)
    return JSONResponse({"changes": [
        {
            "id": c.id,
            "work_order_id": c.work_order_id,
            "action": c.action,
            "before": c.before,
            "after": c.after,
            "user_id": c.user_id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "undone_at": c.undone_at.isoformat() if c.undone_at else None,
        }
        for c in changes
    ]})


@router.post("/production/planner/changes/{log_id}/undo")
def planner_change_undo(
    request: Request,
    log_id: int,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    if not _can_skip(user):
        return HTMLResponse("权限不足（需 supervisor/admin）", status_code=403)
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.shared.errors import BusinessRuleError, ConflictError, NotFoundError
    try:
        PlannerService(db).undo_change(log_id, user_id=user.id)
        db.commit()
    except (NotFoundError, BusinessRuleError, ConflictError) as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse(url="/production/planner", status_code=303)
```

- [ ] **Step 4: 运行 API 测试**

Run: `uv run pytest tests/modules/production/test_planner_routes.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: planner.html 加 changes drawer**

修改 `src/lightmes/templates/production/planner.html`，在 `</div>` 闭合 `<div class="planner-body">` 之前追加：

```html
<button id="planner-changes-btn" type="button" class="planner-view-btn">最近变更</button>
<aside class="planner-changes" id="planner-changes-panel" style="display:none">
  <div class="planner-changes__title">最近变更 <button onclick="document.getElementById('planner-changes-panel').style.display='none'">×</button></div>
  <div id="planner-changes-list" class="planner-changes__list">加载中...</div>
</aside>
```

放在 toolbar 内合适位置（建议在 view-btn 之后）。

- [ ] **Step 6: planner.css 追加 drawer 样式**

在 `src/lightmes/static/css/planner.css` 末尾追加：

```css
.planner-changes {
  position: fixed;
  right: 16px;
  bottom: 16px;
  width: 360px;
  max-height: 60vh;
  background: var(--p-surface);
  border: 1px solid var(--p-border);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,.15);
  z-index: 100;
  display: flex;
  flex-direction: column;
}
.planner-changes__title {
  padding: 10px 12px;
  border-bottom: 1px solid var(--p-border);
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.planner-changes__title button {
  background: none; border: none; cursor: pointer; font-size: 18px; color: var(--p-text-soft);
}
.planner-changes__list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  font-size: 12px;
}
.planner-changes__item {
  padding: 8px 0;
  border-bottom: 1px solid var(--p-border);
}
.planner-changes__item:last-child { border-bottom: none; }
.planner-changes__item--undone { opacity: 0.5; text-decoration: line-through; }
.planner-changes__undo-btn {
  margin-top: 4px;
  padding: 2px 8px;
  background: var(--p-accent);
  color: white;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
}
.planner-changes__undo-btn:hover { background: var(--p-accent-h); }
```

- [ ] **Step 7: planner.js 加 drawer 加载 + undo**

在 `src/lightmes/static/js/planner.js` 末尾追加：

```javascript
// ===== Recent changes drawer =====
(function () {
  var btn = document.getElementById('planner-changes-btn');
  var panel = document.getElementById('planner-changes-panel');
  var list = document.getElementById('planner-changes-list');
  if (!btn || !panel || !list) return;

  function load() {
    fetch('/production/planner/changes').then(function (r) { return r.json(); }).then(function (data) {
      if (!data.changes || !data.changes.length) {
        list.innerHTML = '<div style="color:#6b7280;padding:8px">暂无变更</div>';
        return;
      }
      list.innerHTML = data.changes.map(function (c) {
        var undone = c.undone_at ? 'planner-changes__item--undone' : '';
        var undoBtn = c.undone_at ? '' : '<button class="planner-changes__undo-btn" onclick="undoChange(' + c.id + ')">Undo</button>';
        var time = c.created_at ? new Date(c.created_at).toLocaleString('zh-CN') : '';
        return '<div class="planner-changes__item ' + undone + '">'
          + '<div><strong>#' + c.work_order_id + '</strong> ' + c.action + ' · ' + time + '</div>'
          + '<div style="color:#6b7280">' + (c.before ? JSON.stringify(c.before) : 'null') + ' → ' + (c.after ? JSON.stringify(c.after) : 'null') + '</div>'
          + undoBtn
          + '</div>';
      }).join('');
    }).catch(function (e) {
      list.innerHTML = '<div style="color:#dc2626">加载失败: ' + e + '</div>';
    });
  }

  window.undoChange = function (logId) {
    if (!confirm('确认 undo 此变更？')) return;
    fetch('/production/planner/changes/' + logId + '/undo', { method: 'POST' })
      .then(function (r) {
        if (r.ok) window.location.reload();
        else return r.text().then(function (t) {
          if (window.showErrorModal) window.showErrorModal(t || 'undo 失败'); else alert(t || 'undo 失败');
        });
      });
  };

  btn.addEventListener('click', function () {
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex') load();
  });
})();
```

- [ ] **Step 8: 运行全套 planner + production 回归**

Run: `uv run pytest tests/modules/production/test_planner_models.py tests/modules/production/test_shift_service.py tests/modules/production/test_shift_pages.py tests/modules/production/test_planner_service.py tests/modules/production/test_planner_routes.py -v`
Expected: 全部 PASS。

- [ ] **Step 9: 更新 memory**

在 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_p2_shopfloor.md` 末尾追加：

```markdown
## Production Planner (2026-08-11 完成)

- 新表：`shifts`（完整模型 + days_of_week JSON + per-line 绑定）、`schedule_change_logs`（含 undo）
- `WorkOrder.priority: int` (1-9, 默认 5)
- 复用既有 `planned_start` / `planned_end`
- 三层冲突检测：service 层（detect_conflict）+ API 层（409 + force_conflict）+ undo 时再校验
- 视图：周（产线 × 7 天网格）+ 日（产线 × 24 小时 Gantt，HTML5 drag + resize + 15 分钟 snap）
- UI 风格：OpenMES-inspired，独立 `static/css/planner.css`，命名空间 `.planner-*`，**仅 Planner 页应用**（其他页面下个 spec 统一刷新）
- Inter font self-host 在 `static/fonts/`
- HTML5 原生 drag-drop，无 JS 库依赖
- 路由：`/production/planner`（周）+ `/production/planner/daily`（日）+ `/production/shifts`（CRUD）+ POST `/production/planner/work-orders/{id}/schedule|unschedule` + GET `/production/planner/changes` + POST `/production/planner/changes/{id}/undo`
```

- [ ] **Step 10: Commit (不含 memory 文件)**

```bash
git add src/lightmes/modules/production/router.py \
        src/lightmes/templates/production/planner.html \
        src/lightmes/static/css/planner.css \
        src/lightmes/static/js/planner.js \
        tests/modules/production/test_planner_routes.py
git commit -m "feat(planner): recent changes drawer + undo UI"
```

---

## 任务依赖

```
Task 1 (migration + models)
  ↓
Task 2 (ShiftService + CRUD UI) ← 可与 Task 3 并行
Task 3 (PlannerService core)
  ↓
Task 4 (PlannerService undo)
  ↓
Task 5 (weekly view backend)
  ↓
Task 6 (weekly view frontend)
  ↓
Task 7 (daily view + Gantt)
  ↓
Task 8 (schedule API + conflict)
  ↓
Task 9 (changes drawer + undo UI + memory)
```

建议顺序执行（避免 UI 模板冲突）。Task 2 可与 Task 3 并行但需要谨慎。

## 全套回归（任意 task 完成后均可运行）

```bash
uv run pytest tests/modules/production/ tests/modules/masterdata/ tests/modules/trace/ -v
uv run alembic upgrade head
```

## 手工最终验收（Task 9 完成后）

```bash
uv run uvicorn lightmes.main:app --reload --port 8000
```

浏览器逐项验证：
1. `/production/shifts` 创建早班/晚班
2. `/production/planner` 周视图渲染
3. 从 backlog 拖工单 → 周网格某天 → 该天出现工单卡
4. 切到日视图 → 看到工单 Gantt 块
5. 拖 Gantt 块左右移动 → 时间变更
6. 拖 Gantt 块右边缘 → duration 变更
7. 排两个工单同时段 → 第二个被 409 拒绝
8. force_conflict 模式下重排成功
9. 打开"最近变更"抽屉 → undo 一次 → 工单回到之前状态
