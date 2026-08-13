# Issue / Andon 异常管理系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建独立 Issue 模块：Issue + IssueType + IssueAction(CAPA) 三表 + 状态机（open→acknowledged→resolved→closed + reopen）+ SN 级阻断 station 集成 + defect 联动 + 4 个 MCP 工具。

**Architecture:** 新建 `src/lightmes/modules/issue/` 独立模块（不并入 quality）。Service 层封装状态机 + CAPA 验证闸 + SN 阻断检查；通过修改 `OperationPassService.pass_operation` 注入阻断点；通过 `DefectService.log_defect` 加 `create_issue` 参数实现联动。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2+HTMX, PostgreSQL, pytest

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（127.0.0.1 not localhost）
- Tests 用 `db_session` fixture（SAVEPOINT 隔离）+ `client` fixture（FastAPI TestClient）
- Service raises `DomainError` 子类 from `lightmes.shared.errors`（BusinessRuleError=422, NotFoundError=404, ValidationError=400, ConflictError=409）
- 文案 Chinese for all user-facing strings
- 当前 HEAD migration ID = `f2b8d4e97a1c`（add_opcua_modbus_connections），Task 1 的 down_revision
- 字符串 enum 用 `String + CheckConstraint`，不用 PG ENUM type（对齐 LightMES 既有模式）
- Admin routes 用 `Depends(require_role("admin", "supervisor"))`
- 3-arg `templates.TemplateResponse(request, name, context)` form（2-arg crashes per prior tasks）
- 现有 `DefectService.log_defect` 签名不可破坏（4 个调用点：quality router、MCP tool、connectivity action_executor、operation_pass_service 内部）
- 现有 `OperationPassService.pass_operation` 签名不可破坏
- CSS 用 `app.css` 的 token（不复用 planner.css），HTML 模板复用 .card / .form-row / .data-table / .badge 等既有 class

---

### Task 1: Migration + Models + Seed

**Files:**
- Create: `src/lightmes/modules/issue/__init__.py`
- Create: `src/lightmes/modules/issue/models.py`
- Create: `src/lightmes/migrations/versions/a1b9c2d3e4f5_add_issue_andon.py`
- Modify: `src/lightmes/shared/base.py`（如需注册新 model —— 通常 `from lightmes.modules.issue import models` 即可，但确保 main.py 启动时加载）
- Test: `tests/modules/issue/__init__.py`（空）
- Test: `tests/modules/issue/test_models.py`

**Interfaces:**
- Consumes: `lightmes.shared.base.Base` + `TimestampMixin`
- Produces: `IssueType` / `Issue` / `IssueAction` SQLAlchemy models + 3 张表 + 6 个默认 type seed

- [ ] **Step 1: 创建模块骨架**

```bash
mkdir -p src/lightmes/modules/issue
touch src/lightmes/modules/issue/__init__.py
mkdir -p tests/modules/issue
touch tests/modules/issue/__init__.py
```

`src/lightmes/modules/issue/__init__.py` 内容：

```python
from lightmes.modules.issue import models, repository, service, router  # noqa: F401
```

`src/lightmes/modules/issue/__init__.py` 暂时为空（后续 task 填充）：

```python
"""Issue / Andon 异常管理模块."""
```

- [ ] **Step 2: 写 models.py**

`src/lightmes/modules/issue/models.py`：

```python
from datetime import date, datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from lightmes.shared.base import Base, TimestampMixin


class IssueType(Base, TimestampMixin):
    __tablename__ = "issue_types"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'minor', 'major', 'critical')",
            name="ck_issue_types_severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10))
    is_blocking: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'closed')",
            name="ck_issues_status"),
        CheckConstraint(
            "severity IN ('info', 'minor', 'major', 'critical')",
            name="ck_issues_severity"),
        CheckConstraint(
            "source IN ('station_andon', 'defect_linked', 'manual')",
            name="ck_issues_source"),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('use_as_is', 'rework', 'scrap', 'hold')",
            name="ck_issues_disposition"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_type_id: Mapped[int] = mapped_column(
        ForeignKey("issue_types.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20), default="manual")
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
    reported_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(15), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    containment_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IssueAction(Base, TimestampMixin):
    __tablename__ = "issue_actions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('corrective', 'preventive', 'containment')",
            name="ck_issue_actions_type"),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'done', 'verified')",
            name="ck_issue_actions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(15))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="open")
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 3: 写 models 测试**

`tests/modules/issue/test_models.py`：

```python
from datetime import datetime

from lightmes.modules.issue.models import Issue, IssueAction, IssueType


def test_issue_type_defaults(db_session):
    """新建 IssueType 默认 is_blocking=False / is_active=True。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it)
    db_session.flush()
    assert it.is_blocking is False
    assert it.is_active is True


def test_issue_defaults(db_session, sample_user):
    """新建 Issue 默认 status=open / source=manual。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it)
    db_session.flush()
    issue = Issue(
        issue_type_id=it.id, title="t", severity="minor",
        reported_by_id=sample_user.id,
    )
    db_session.add(issue)
    db_session.flush()
    assert issue.status == "open"
    assert issue.source == "manual"
    assert issue.reported_at is not None


def test_issue_action_defaults(db_session, sample_user):
    """新建 IssueAction 默认 status=open。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=sample_user.id)
    db_session.add(issue); db_session.flush()
    action = IssueAction(issue_id=issue.id, type="corrective", title="a")
    db_session.add(action); db_session.flush()
    assert action.status == "open"
```

注意：`sample_user` fixture 来自 `tests/conftest.py`，如不存在需先在 conftest 创建（见 Step 6）。

- [ ] **Step 4: 运行测试，确认 FAIL（因 sample_user fixture 可能缺失或表未建）**

Run: `uv run pytest tests/modules/issue/test_models.py -v`
Expected: FAIL（ImportError 或 fixture not found）

- [ ] **Step 5: 确保 conftest 有 sample_user fixture**

检查 `tests/conftest.py`，若无 `sample_user` fixture 在末尾追加：

```python
@pytest.fixture
def sample_user(db_session):
    """提供测试用的已登录 user。"""
    from lightmes.modules.auth.models import User, Role
    from lightmes.modules.auth.service import AuthService
    auth = AuthService(db_session)
    role = db_session.execute(
        select(Role).where(Role.name == "admin")
    ).scalar_one_or_none()
    if role is None:
        role = Role(name="admin", description="admin")
        db_session.add(role); db_session.flush()
    user = User(
        username="_test_sample_user", password_hash="x",
        display_name="Test", role_id=role.id, is_active=True,
    )
    db_session.add(user); db_session.flush()
    return user
```

Run: `uv run pytest tests/modules/issue/test_models.py -v`
Expected: 3 tests FAIL with `undefined table`（issue_types / issues / issue_actions 不存在）

- [ ] **Step 6: 写 migration**

`src/lightmes/migrations/versions/a1b9c2d3e4f5_add_issue_andon.py`：

```python
"""add issue andon

Revision ID: a1b9c2d3e4f5
Revises: f2b8d4e97a1c
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b9c2d3e4f5"
down_revision = "f2b8d4e97a1c"
branch_labels = None
depends_on = None


SEED_TYPES = [
    {"code": "material_shortage", "name": "缺料", "severity": "major", "is_blocking": True, "is_active": True, "description": "缺料异常"},
    {"code": "quality", "name": "质量异常", "severity": "major", "is_blocking": False, "is_active": True, "description": "质量异常"},
    {"code": "tool_failure", "name": "工装失效", "severity": "major", "is_blocking": True, "is_active": True, "description": "工装/夹具失效"},
    {"code": "equipment_fault", "name": "设备故障", "severity": "critical", "is_blocking": True, "is_active": True, "description": "设备故障"},
    {"code": "safety", "name": "安全问题", "severity": "critical", "is_blocking": True, "is_active": True, "description": "EHS 相关"},
    {"code": "other", "name": "其他", "severity": "minor", "is_blocking": False, "is_active": True, "description": "其他"},
]


def upgrade():
    op.create_table(
        "issue_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_issue_types_code"),
        sa.CheckConstraint("severity IN ('info', 'minor', 'major', 'critical')", name="ck_issue_types_severity"),
    )

    op.create_table(
        "issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_type_id", sa.Integer(), sa.ForeignKey("issue_types.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("serial_unit_id", sa.Integer(), sa.ForeignKey("serial_units.id"), nullable=True),
        sa.Column("work_order_id", sa.Integer(), sa.ForeignKey("work_orders.id"), nullable=True),
        sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=True),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("defect_id", sa.Integer(), sa.ForeignKey("defect_records.id"), nullable=True),
        sa.Column("reported_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("acknowledged_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(15), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("containment_action", sa.Text(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved', 'closed')", name="ck_issues_status"),
        sa.CheckConstraint("severity IN ('info', 'minor', 'major', 'critical')", name="ck_issues_severity"),
        sa.CheckConstraint("source IN ('station_andon', 'defect_linked', 'manual')", name="ck_issues_source"),
        sa.CheckConstraint("disposition IS NULL OR disposition IN ('use_as_is', 'rework', 'scrap', 'hold')", name="ck_issues_disposition"),
    )
    op.create_index("ix_issues_status", "issues", ["status"])
    op.create_index("ix_issues_serial_unit_id", "issues", ["serial_unit_id"])
    op.create_index("ix_issues_work_order_id", "issues", ["work_order_id"])
    op.create_index("ix_issues_work_station_id", "issues", ["work_station_id"])

    op.create_table(
        "issue_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.Integer(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(15), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assigned_to_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("type IN ('corrective', 'preventive', 'containment')", name="ck_issue_actions_type"),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'done', 'verified')", name="ck_issue_actions_status"),
    )
    op.create_index("ix_issue_actions_issue_id", "issue_actions", ["issue_id"])

    # Seed 默认类型
    issue_types = sa.table(
        "issue_types",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("severity", sa.String),
        sa.column("is_blocking", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(issue_types, SEED_TYPES)


def downgrade():
    op.drop_table("issue_actions")
    op.drop_table("issues")
    op.drop_table("issue_types")
```

- [ ] **Step 7: 跑 migration + 测试**

Run:
```bash
cd src/lightmes && alembic upgrade head
```
（或项目根的 alembic.ini 配置路径，按现有项目惯例）

Expected: 3 张表创建成功，6 行 issue_types 数据。

Run: `uv run pytest tests/modules/issue/test_models.py -v`
Expected: 3 PASS

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/issue/__init__.py src/lightmes/modules/issue/models.py \
        src/lightmes/migrations/versions/a1b9c2d3e4f5_add_issue_andon.py \
        tests/modules/issue/__init__.py tests/modules/issue/test_models.py \
        tests/conftest.py
git commit -m "feat(issue): models + migration + 6 default type seeds"
```

---

### Task 2: Repository 层

**Files:**
- Create: `src/lightmes/modules/issue/repository.py`
- Test: `tests/modules/issue/test_repository.py`

**Interfaces:**
- Consumes: Task 1 的 Issue / IssueType / IssueAction models
- Produces: `IssueRepository`（CRUD + 按 SN/状态/WO 过滤）+ `IssueTypeRepository` + `IssueActionRepository`

- [ ] **Step 1: 写 repository.py**

```python
from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.issue.models import Issue, IssueAction, IssueType


class IssueTypeRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_active(self) -> list[IssueType]:
        return list(self.db.execute(
            select(IssueType)
            .where(IssueType.is_active.is_(True))
            .order_by(IssueType.severity.desc(), IssueType.code)
        ).scalars().all())

    def get(self, type_id: int) -> IssueType | None:
        return self.db.get(IssueType, type_id)

    def get_by_code(self, code: str) -> IssueType | None:
        return self.db.execute(
            select(IssueType).where(IssueType.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[IssueType]:
        return list(self.db.execute(
            select(IssueType).order_by(IssueType.code)
        ).scalars().all())


class IssueRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, issue_id: int) -> Issue | None:
        return self.db.get(Issue, issue_id)

    def list(
        self,
        *,
        statuses: list[str] | None = None,
        severities: list[str] | None = None,
        sources: list[str] | None = None,
        work_station_id: int | None = None,
        work_order_id: int | None = None,
        serial_unit_id: int | None = None,
        reported_by_id: int | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> list[Issue]:
        q = select(Issue).order_by(Issue.status, Issue.id.desc())
        if statuses:
            q = q.where(Issue.status.in_(statuses))
        if severities:
            q = q.where(Issue.severity.in_(severities))
        if sources:
            q = q.where(Issue.source.in_(sources))
        if work_station_id is not None:
            q = q.where(Issue.work_station_id == work_station_id)
        if work_order_id is not None:
            q = q.where(Issue.work_order_id == work_order_id)
        if serial_unit_id is not None:
            q = q.where(Issue.serial_unit_id == serial_unit_id)
        if reported_by_id is not None:
            q = q.where(Issue.reported_by_id == reported_by_id)
        if search:
            q = q.where(Issue.title.ilike(f"%{search}%"))
        return list(self.db.execute(
            q.offset((page - 1) * size).limit(size)
        ).scalars().all())

    def count_open_blocking_for_sn(self, serial_unit_id: int) -> int:
        """返回该 SN 当前阻断中的 issue 数量。"""
        return self.db.execute(
            select(Issue)
            .join(IssueType)
            .where(
                Issue.serial_unit_id == serial_unit_id,
                Issue.status.in_(["open", "acknowledged"]),
                IssueType.is_blocking.is_(True),
            )
        ).scalars().all().__len__()

    def get_blocking_for_sn(self, serial_unit_id: int) -> Issue | None:
        """返回最新的阻断 issue，无则 None。"""
        return self.db.execute(
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

    def count_open(self) -> int:
        return self.db.execute(
            select(Issue).where(Issue.status.in_(["open", "acknowledged"]))
        ).scalars().all().__len__()

    def count_blocking(self) -> int:
        return self.db.execute(
            select(Issue)
            .join(IssueType)
            .where(
                Issue.status.in_(["open", "acknowledged"]),
                IssueType.is_blocking.is_(True),
            )
        ).scalars().all().__len__()

    def add(self, issue: Issue) -> Issue:
        self.db.add(issue)
        self.db.flush()
        return issue


class IssueActionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, action_id: int) -> IssueAction | None:
        return self.db.get(IssueAction, action_id)

    def list_for_issue(self, issue_id: int) -> list[IssueAction]:
        return list(self.db.execute(
            select(IssueAction)
            .where(IssueAction.issue_id == issue_id)
            .order_by(IssueAction.id)
        ).scalars().all())

    def count_unverified(self, issue_id: int) -> int:
        return self.db.execute(
            select(IssueAction)
            .where(
                IssueAction.issue_id == issue_id,
                IssueAction.status != "verified",
            )
        ).scalars().all().__len__()

    def add(self, action: IssueAction) -> IssueAction:
        self.db.add(action)
        self.db.flush()
        return action
```

- [ ] **Step 2: 写 repository 测试**

`tests/modules/issue/test_repository.py`：

```python
from lightmes.modules.issue.models import Issue, IssueAction, IssueType
from lightmes.modules.issue.repository import (
    IssueRepository, IssueActionRepository, IssueTypeRepository,
)


def test_get_blocking_for_sn_returns_none_when_no_issue(db_session, sample_user):
    repo = IssueRepository(db_session)
    assert repo.get_blocking_for_sn(serial_unit_id=999) is None


def test_get_blocking_for_sn_returns_open_blocking(db_session, sample_user):
    """is_blocking=true + status=open 命中；is_blocking=false 不命中。"""
    type_repo = IssueTypeRepository(db_session)
    it_block = IssueType(code="B", name="B", severity="critical", is_blocking=True)
    it_non = IssueType(code="N", name="N", severity="minor", is_blocking=False)
    db_session.add_all([it_block, it_non]); db_session.flush()

    repo = IssueRepository(db_session)
    blocking = Issue(
        issue_type_id=it_block.id, title="b", severity="critical",
        source="manual", serial_unit_id=42,
        reported_by_id=sample_user.id)
    non_block = Issue(
        issue_type_id=it_non.id, title="n", severity="minor",
        source="manual", serial_unit_id=42,
        reported_by_id=sample_user.id)
    db_session.add_all([blocking, non_block]); db_session.flush()

    found = repo.get_blocking_for_sn(42)
    assert found is not None
    assert found.id == blocking.id


def test_count_unverified(db_session, sample_user):
    """验证 count_unverified 正确计数未 verify 的 action。"""
    it = IssueType(code="X", name="X", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=sample_user.id)
    db_session.add(issue); db_session.flush()
    a1 = IssueAction(issue_id=issue.id, type="corrective", title="a1", status="verified")
    a2 = IssueAction(issue_id=issue.id, type="corrective", title="a2", status="open")
    a3 = IssueAction(issue_id=issue.id, type="corrective", title="a3", status="in_progress")
    db_session.add_all([a1, a2, a3]); db_session.flush()

    repo = IssueActionRepository(db_session)
    assert repo.count_unverified(issue.id) == 2
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/modules/issue/test_repository.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add src/lightmes/modules/issue/repository.py tests/modules/issue/test_repository.py
git commit -m "feat(issue): repository layer with SN blocking query"
```

---

### Task 3: Service 层 — 状态机 + CAPA

**Files:**
- Create: `src/lightmes/modules/issue/service.py`
- Test: `tests/modules/issue/test_service.py`

**Interfaces:**
- Consumes: Task 1 models + Task 2 repositories
- Produces: `IssueService` 含 `create_issue` / `acknowledge` / `resolve` / `close` / `reopen` / `add_action` / `start_action` / `complete_action` / `verify_action` / `is_blocking` / `check_block_for_sn` / `create_from_defect`

- [ ] **Step 1: 写 service.py**

```python
from datetime import datetime
from sqlalchemy.orm import Session

from lightmes.modules.issue.models import Issue, IssueAction, IssueType
from lightmes.modules.issue.repository import (
    IssueActionRepository, IssueRepository, IssueTypeRepository,
)
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


SEVERITY_MAP = {"critical": "critical", "major": "major", "minor": "minor"}


class IssueService:
    def __init__(self, db: Session):
        self.db = db
        self.types = IssueTypeRepository(db)
        self.issues = IssueRepository(db)
        self.actions = IssueActionRepository(db)

    # ---------- 查询辅助 ----------

    @staticmethod
    def is_blocking(issue: Issue) -> bool:
        return (
            issue.issue_type.is_blocking
            and issue.status in ("open", "acknowledged")
        )

    def check_block_for_sn(self, serial_unit_id: int) -> Issue | None:
        """SN 级阻断检查：返回最新阻断 Issue，无则 None。"""
        return self.issues.get_blocking_for_sn(serial_unit_id)

    def _get_or_404(self, issue_id: int) -> Issue:
        issue = self.issues.get(issue_id)
        if issue is None:
            raise NotFoundError(f"Issue 不存在: {issue_id}")
        return issue

    # ---------- 状态机 ----------

    def create_issue(
        self,
        *,
        issue_type_id: int,
        title: str,
        reported_by_id: int,
        description: str | None = None,
        source: str = "manual",
        serial_unit_id: int | None = None,
        work_order_id: int | None = None,
        work_station_id: int | None = None,
        operation_id: int | None = None,
        defect_id: int | None = None,
    ) -> Issue:
        """创建 Issue。severity 从 type snapshot；is_blocking 跟 type 走。"""
        if not title.strip():
            raise ValidationError("title 不可为空")
        it = self.types.get(issue_type_id)
        if it is None or not it.is_active:
            raise NotFoundError(f"IssueType 不存在或已停用: {issue_type_id}")
        issue = Issue(
            issue_type_id=issue_type_id,
            title=title.strip(),
            description=description,
            status="open",
            severity=it.severity,
            source=source,
            serial_unit_id=serial_unit_id,
            work_order_id=work_order_id,
            work_station_id=work_station_id,
            operation_id=operation_id,
            defect_id=defect_id,
            reported_by_id=reported_by_id,
        )
        return self.issues.add(issue)

    def acknowledge(self, issue_id: int, user_id: int) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "open":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 acknowledge")
        issue.status = "acknowledged"
        issue.acknowledged_by_id = user_id
        issue.acknowledged_at = datetime.now()
        return issue

    def resolve(
        self,
        issue_id: int,
        user_id: int,
        *,
        root_cause: str,
        containment_action: str,
        disposition: str,
        resolution_notes: str | None = None,
    ) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "acknowledged":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 resolve")
        if not root_cause.strip() or not containment_action.strip():
            raise ValidationError("root_cause 与 containment_action 不可为空")
        if disposition not in ("use_as_is", "rework", "scrap", "hold"):
            raise ValidationError(f"非法 disposition: {disposition}")
        issue.status = "resolved"
        issue.root_cause = root_cause
        issue.containment_action = containment_action
        issue.disposition = disposition
        issue.resolution_notes = resolution_notes
        issue.resolved_by_id = user_id
        issue.resolved_at = datetime.now()
        return issue

    def close(self, issue_id: int, user_id: int) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "resolved":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 close")
        # CAPA 验证闸
        unverified = self.actions.count_unverified(issue_id)
        if unverified > 0:
            raise BusinessRuleError(
                f"还有 {unverified} 条 CAPA 未 verified，不可 close")
        issue.status = "closed"
        issue.closed_by_id = user_id
        issue.closed_at = datetime.now()
        return issue

    def reopen(self, issue_id: int, user_id: int, *, reason: str) -> Issue:
        issue = self._get_or_404(issue_id)
        if issue.status != "closed":
            raise BusinessRuleError(f"当前状态 {issue.status} 不可 reopen")
        if not reason.strip():
            raise ValidationError("reopen 须提供 reason")
        issue.status = "open"
        issue.reopen_reason = reason
        # 清除 resolved/closed 时间戳（保留 acknowledged/resolved 字段不动，作为历史）
        issue.closed_by_id = None
        issue.closed_at = None
        return issue

    # ---------- CAPA ----------

    def add_action(
        self,
        issue_id: int,
        *,
        type: str,
        title: str,
        description: str | None = None,
        assigned_to_id: int | None = None,
        due_date=None,
    ) -> IssueAction:
        issue = self._get_or_404(issue_id)
        if issue.status == "closed":
            raise BusinessRuleError("closed 的 Issue 不可加 CAPA（先 reopen）")
        if type not in ("corrective", "preventive", "containment"):
            raise ValidationError(f"非法 CAPA type: {type}")
        if not title.strip():
            raise ValidationError("title 不可为空")
        action = IssueAction(
            issue_id=issue_id,
            type=type,
            title=title.strip(),
            description=description,
            assigned_to_id=assigned_to_id,
            due_date=due_date,
            status="open",
        )
        return self.actions.add(action)

    def _transition_action(
        self, action_id: int, from_statuses: list[str], to_status: str,
        user_id: int, *, role_check: bool = False,
    ) -> IssueAction:
        action = self.actions.get(action_id)
        if action is None:
            raise NotFoundError(f"IssueAction 不存在: {action_id}")
        if action.status not in from_statuses:
            raise BusinessRuleError(
                f"CAPA 当前状态 {action.status} 不可转 {to_status}")
        action.status = to_status
        if to_status == "in_progress":
            pass  # 无时间戳
        elif to_status == "done":
            action.completed_at = datetime.now()
            action.completed_by_id = user_id
        elif to_status == "verified":
            action.verified_at = datetime.now()
            action.verified_by_id = user_id
        return action

    def start_action(self, action_id: int, user_id: int) -> IssueAction:
        """assignee 或 supervisor+ 可调用（角色检查在 router）。"""
        return self._transition_action(
            action_id, ["open"], "in_progress", user_id)

    def complete_action(self, action_id: int, user_id: int) -> IssueAction:
        return self._transition_action(
            action_id, ["open", "in_progress"], "done", user_id)

    def verify_action(self, action_id: int, user_id: int) -> IssueAction:
        return self._transition_action(
            action_id, ["done"], "verified", user_id)

    # ---------- Defect 联动 ----------

    def create_from_defect(self, defect, *, reported_by_id: int) -> Issue:
        """从 DefectRecord 派生 Issue。同事务调用，失败回滚。"""
        quality_type = self.types.get_by_code("quality")
        if quality_type is None:
            raise BusinessRuleError("IssueType 'quality' 未 seed，无法联动")
        from lightmes.modules.production.models import DefectRecord  # 局部 import 避免循环
        assert isinstance(defect, DefectRecord)
        title = f"缺陷上报: {defect.defect_type_name} (SN {defect.serial_unit_id})"
        return self.create_issue(
            issue_type_id=quality_type.id,
            title=title,
            description=defect.remark or "",
            source="defect_linked",
            serial_unit_id=defect.serial_unit_id,
            work_order_id=defect.work_order_id,
            work_station_id=defect.work_station_id,
            operation_id=defect.operation_id,
            defect_id=defect.id,
            reported_by_id=reported_by_id,
        )
```

- [ ] **Step 2: 写 service 测试（happy path + 边界）**

`tests/modules/issue/test_service.py`：

```python
import pytest
from lightmes.modules.issue.models import Issue, IssueAction, IssueType
from lightmes.modules.issue.service import IssueService
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError


@pytest.fixture
def type_minor(db_session):
    it = IssueType(code="T_minor", name="minor", severity="minor", is_blocking=False)
    db_session.add(it); db_session.flush()
    return it


@pytest.fixture
def type_block(db_session):
    it = IssueType(code="T_block", name="block", severity="critical", is_blocking=True)
    db_session.add(it); db_session.flush()
    return it


@pytest.fixture
def svc(db_session):
    return IssueService(db_session)


def test_create_issue_snapshots_severity_from_type(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    assert issue.severity == "minor"
    assert issue.status == "open"
    assert issue.source == "manual"


def test_create_issue_empty_title_rejected(svc, type_minor, sample_user):
    with pytest.raises(ValidationError):
        svc.create_issue(
            issue_type_id=type_minor.id, title="  ",
            reported_by_id=sample_user.id)


def test_acknowledge_requires_open(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    assert issue.status == "acknowledged"
    # 再 ack 应失败
    with pytest.raises(BusinessRuleError):
        svc.acknowledge(issue.id, sample_user.id)


def test_resolve_requires_all_fields(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    with pytest.raises(ValidationError):
        svc.resolve(issue.id, sample_user.id,
                    root_cause="", containment_action="x",
                    disposition="rework")
    with pytest.raises(ValidationError):
        svc.resolve(issue.id, sample_user.id,
                    root_cause="x", containment_action="x",
                    disposition="bogus")


def test_close_blocked_when_unverified_capa(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    svc.add_action(issue.id, type="corrective", title="a")  # 默认 status=open
    with pytest.raises(BusinessRuleError):
        svc.close(issue.id, sample_user.id)


def test_close_passes_when_all_capa_verified(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    a = svc.add_action(issue.id, type="corrective", title="a")
    svc.start_action(a.id, sample_user.id)
    svc.complete_action(a.id, sample_user.id)
    svc.verify_action(a.id, sample_user.id)
    svc.close(issue.id, sample_user.id)
    assert issue.status == "closed"


def test_reopen_only_from_closed(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    with pytest.raises(BusinessRuleError):
        svc.reopen(issue.id, sample_user.id, reason="x")


def test_reopen_requires_reason(svc, type_minor, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    svc.close(issue.id, sample_user.id)
    with pytest.raises(ValidationError):
        svc.reopen(issue.id, sample_user.id, reason="")


def test_is_blocking_combines_type_and_status(svc, type_block, sample_user):
    issue = svc.create_issue(
        issue_type_id=type_block.id, title="t",
        serial_unit_id=42, reported_by_id=sample_user.id)
    assert svc.is_blocking(issue) is True
    svc.acknowledge(issue.id, sample_user.id)
    assert svc.is_blocking(issue) is True  # acknowledged 仍阻断
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    assert svc.is_blocking(issue) is False  # resolved 不阻断


def test_check_block_for_sn_returns_latest(svc, type_block, sample_user):
    """多条 blocking 返回 id 最大的（最新）。"""
    older = svc.create_issue(
        issue_type_id=type_block.id, title="old",
        serial_unit_id=42, reported_by_id=sample_user.id)
    newer = svc.create_issue(
        issue_type_id=type_block.id, title="new",
        serial_unit_id=42, reported_by_id=sample_user.id)
    found = svc.check_block_for_sn(42)
    assert found.id == newer.id


def test_capa_lifecycle_full(svc, type_minor, sample_user):
    """open → in_progress → done → verified 全链。"""
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    a = svc.add_action(issue.id, type="corrective", title="a")
    assert a.status == "open"

    svc.start_action(a.id, sample_user.id)
    assert a.status == "in_progress"

    svc.complete_action(a.id, sample_user.id)
    assert a.status == "done"
    assert a.completed_at is not None

    svc.verify_action(a.id, sample_user.id)
    assert a.status == "verified"
    assert a.verified_at is not None


def test_capa_verify_requires_done(svc, type_minor, sample_user):
    """未 done 直接 verify 失败。"""
    issue = svc.create_issue(
        issue_type_id=type_minor.id, title="t",
        reported_by_id=sample_user.id)
    a = svc.add_action(issue.id, type="corrective", title="a")
    with pytest.raises(BusinessRuleError):
        svc.verify_action(a.id, sample_user.id)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/modules/issue/test_service.py -v`
Expected: 12 PASS

- [ ] **Step 4: Commit**

```bash
git add src/lightmes/modules/issue/service.py tests/modules/issue/test_service.py
git commit -m "feat(issue): service layer with state machine + CAPA + defect linkage"
```

---

### Task 4: Jinja `issue_linkify` filter

**Files:**
- Modify: `src/lightmes/main.py`（注册 filter 到 Jinja2Templates）
- Test: `tests/modules/test_linkify.py`

**Interfaces:**
- Consumes: Jinja2Templates 实例（在 main.py 中）
- Produces: `issue_linkify(text)` filter，HTML safe 输出

- [ ] **Step 1: 写 filter 测试**

`tests/modules/test_linkify.py`：

```python
from lightmes.modules.issue.linkify import issue_linkify


def test_basic():
    assert issue_linkify("see #42 for context") == \
        'see <a href="/issues/42">#42</a> for context'


def test_multiple():
    assert issue_linkify("#1 #2 #3") == \
        '<a href="/issues/1">#1</a> <a href="/issues/2">#2</a> <a href="/issues/3">#3</a>'


def test_does_not_match_hashtag_words():
    """#ABC 不替换（非纯数字）。"""
    assert issue_linkify("#ABC topic") == "#ABC topic"


def test_caps_at_8_digits():
    """#数字 1-8 位才匹配；9 位以上不匹配（避免误匹配大整数）。"""
    assert issue_linkify("#12345678") == '<a href="/issues/12345678">#12345678</a>'
    assert issue_linkify("#123456789") == "#123456789"


def test_empty_input():
    assert issue_linkify(None) == ""
    assert issue_linkify("") == ""


def test_xss_escape():
    """用户输入的 <script> 应被转义。"""
    result = issue_linkify("<script>x</script> #1")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert '<a href="/issues/1">#1</a>' in result
```

- [ ] **Step 2: 运行测试，确认 FAIL**

Run: `uv run pytest tests/modules/test_linkify.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'lightmes.modules.issue.linkify'`）

- [ ] **Step 3: 写 linkify.py**

`src/lightmes/modules/issue/linkify.py`：

```python
import re
from markupsafe import Markup, escape

_ISSUE_REF = re.compile(r"#(\d{1,8})(?!\d)")


def issue_linkify(text) -> Markup:
    """渲染时把 #数字 替换为 /issues/数字 链接。先 escape 防 XSS。"""
    if not text:
        return Markup("")
    escaped = str(escape(text))
    return Markup(_ISSUE_REF.sub(r'<a href="/issues/\1">#\1</a>', escaped))
```

- [ ] **Step 4: 运行测试，确认 PASS**

Run: `uv run pytest tests/modules/test_linkify.py -v`
Expected: 6 PASS

- [ ] **Step 5: 注册到 Jinja2Templates**

在 `src/lightmes/main.py` 找到 `_templates = Jinja2Templates(...)` 那行（约 76 行）后追加：

```python
from lightmes.modules.issue.linkify import issue_linkify
_templates.env.filters["issue_linkify"] = issue_linkify
```

- [ ] **Step 6: 确认 import 不破坏启动**

Run: `uv run python -c "from lightmes.main import app; print('OK')"`
Expected: 输出 `OK` 无异常

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/issue/linkify.py src/lightmes/main.py tests/modules/test_linkify.py
git commit -m "feat(issue): issue_linkify Jinja filter with XSS escaping"
```

---

### Task 5: Router 层 — List + Detail + Lifecycle

**Files:**
- Create: `src/lightmes/modules/issue/router.py`
- Modify: `src/lightmes/main.py`（注册 router）
- Create: `src/lightmes/templates/issue/base.html`（如需）
- Create: `src/lightmes/templates/issue/list.html`
- Create: `src/lightmes/templates/issue/detail.html`
- Create: `src/lightmes/templates/issue/partials/issue_row.html`
- Test: `tests/modules/issue/test_router.py`

**Interfaces:**
- Consumes: Task 3 service + Task 4 linkify
- Produces: HTML routes `GET /issues` / `GET /issues/{id}` + POST lifecycle endpoints

- [ ] **Step 1: 写 router.py 骨架 + list + detail + lifecycle**

`src/lightmes/modules/issue/router.py`：

```python
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_role
from lightmes.modules.issue.models import Issue, IssueType
from lightmes.modules.issue.repository import (
    IssueActionRepository, IssueRepository, IssueTypeRepository,
)
from lightmes.modules.issue.service import IssueService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _filter_visible(issues: list[Issue], user) -> list[Issue]:
    """operator 仅自己上报的；supervisor+ 全部。"""
    if user is None:
        return []
    if user.role in ("supervisor", "admin") or getattr(user, "role_obj", None) and user.role_obj.name in ("supervisor", "admin"):
        return issues
    return [i for i in issues if i.reported_by_id == user.id]


@router.get("/issues", response_class=HTMLResponse)
def issue_list(
    request: Request,
    status: str = "",
    severity: str = "",
    source: str = "",
    search: str = "",
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})

    repo = IssueRepository(db)
    kwargs = {}
    if status:
        kwargs["statuses"] = status.split(",")
    if severity:
        kwargs["severities"] = severity.split(",")
    if source:
        kwargs["sources"] = source.split(",")
    if search:
        kwargs["search"] = search
    # operator 仅自己
    if user.role == "operator" or (getattr(user, "role_obj", None) and user.role_obj.name == "operator"):
        kwargs["reported_by_id"] = user.id
    issues = repo.list(**kwargs)

    return templates.TemplateResponse(
        request, "issue/list.html",
        {"issues": issues, "filters": {"status": status, "severity": severity,
                                        "source": source, "search": search}})


@router.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(
    request: Request,
    issue_id: int,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})

    svc = IssueService(db)
    issue = svc.issues.get(issue_id)
    if issue is None:
        return Response(status_code=404, content="Issue 不存在")

    # operator 只能看自己上报的
    is_privileged = user.role in ("supervisor", "admin") or (getattr(user, "role_obj", None) and user.role_obj.name in ("supervisor", "admin"))
    if not is_privileged and issue.reported_by_id != user.id:
        return Response(status_code=403, content="无权查看")

    actions = IssueActionRepository(db).list_for_issue(issue_id)
    return templates.TemplateResponse(
        request, "issue/detail.html",
        {"issue": issue, "actions": actions, "is_privileged": is_privileged})


# ---- Lifecycle POST 端点 ----

@router.post("/issues/{issue_id}/acknowledge")
def issue_acknowledge(
    request: Request, issue_id: int,
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).acknowledge(issue_id, user.id)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/resolve")
def issue_resolve(
    request: Request, issue_id: int,
    root_cause: str = Form(...),
    containment_action: str = Form(...),
    disposition: str = Form(...),
    resolution_notes: str = Form(""),
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).resolve(
            issue_id, user.id,
            root_cause=root_cause, containment_action=containment_action,
            disposition=disposition,
            resolution_notes=resolution_notes or None)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/close")
def issue_close(
    request: Request, issue_id: int,
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).close(issue_id, user.id)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/reopen")
def issue_reopen(
    request: Request, issue_id: int,
    reason: str = Form(...),
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).reopen(issue_id, user.id, reason=reason)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})
```

- [ ] **Step 2: 写 list.html 模板**

`src/lightmes/templates/issue/list.html`：

```html
{% extends "base.html" %}
{% block title %}Issue 列表{% endblock %}
{% block content %}
<h1 class="page-title">Issue 看板 <small>异常 / Andon</small></h1>

<div class="card">
  <div class="card__title">过滤</div>
  <form method="get" class="form-row">
    <div class="field"><label>状态</label>
      <select name="status">
        <option value="">全部</option>
        <option value="open" {% if filters.status == 'open' %}selected{% endif %}>open</option>
        <option value="acknowledged" {% if filters.status == 'acknowledged' %}selected{% endif %}>acknowledged</option>
        <option value="resolved" {% if filters.resolved == 'resolved' %}selected{% endif %}>resolved</option>
        <option value="closed" {% if filters.status == 'closed' %}selected{% endif %}>closed</option>
      </select>
    </div>
    <div class="field"><label>严重度</label>
      <select name="severity">
        <option value="">全部</option>
        {% for s in ['info', 'minor', 'major', 'critical'] %}
        <option value="{{ s }}" {% if filters.severity == s %}selected{% endif %}>{{ s }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="field"><label>来源</label>
      <select name="source">
        <option value="">全部</option>
        {% for s in ['station_andon', 'defect_linked', 'manual'] %}
        <option value="{{ s }}" {% if filters.source == s %}selected{% endif %}>{{ s }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="field" style="flex:1;min-width:200px"><label>搜索标题</label>
      <input name="search" value="{{ filters.search }}" placeholder="关键词"></div>
    <button type="submit">过滤</button>
  </form>
</div>

<div class="card">
  <div class="card__title">Issues ({{ issues|length }})</div>
  <table class="data-table">
    <thead><tr><th>#</th><th>标题</th><th>类型</th><th>严重</th><th>状态</th><th>SN</th><th>上报人</th><th>时间</th></tr></thead>
    <tbody>
      {% for i in issues %}
      <tr {% if i.status in ['open', 'acknowledged'] and i.issue_type.is_blocking %}style="border-left:4px solid var(--danger)"{% endif %}>
        <td><a href="/issues/{{ i.id }}">#{{ i.id }}</a></td>
        <td>{{ i.title | issue_linkify }}{% if i.status in ['open','acknowledged'] and i.issue_type.is_blocking %} <span class="badge badge--danger">阻断</span>{% endif %}</td>
        <td><span class="badge">{{ i.issue_type.code }}</span></td>
        <td><span class="badge {% if i.severity == 'critical' %}badge--danger{% elif i.severity == 'major' %}badge--warn{% endif %}">{{ i.severity }}</span></td>
        <td><span class="badge {% if i.status == 'open' %}badge--warn{% elif i.status == 'closed' %}badge--ok{% endif %}">{{ i.status }}</span></td>
        <td>{% if i.serial_unit_id %}<a href="/trace/query?sn={{ i.serial_unit_id }}">SN#{{ i.serial_unit_id }}</a>{% else %}—{% endif %}</td>
        <td>#{{ i.reported_by_id }}</td>
        <td>{{ i.reported_at.strftime('%Y-%m-%d %H:%M') if i.reported_at else '—' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="8" style="color:var(--text-soft);text-align:center">暂无 Issue</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 3: 写 detail.html 模板**

`src/lightmes/templates/issue/detail.html`：

```html
{% extends "base.html" %}
{% block title %}Issue #{{ issue.id }}{% endblock %}
{% block content %}
<h1 class="page-title">#{{ issue.id }} <small>{{ issue.title }}</small></h1>

<div class="card">
  <div class="card__title">概要</div>
  <p>
    <span class="badge {% if issue.severity == 'critical' %}badge--danger{% elif issue.severity == 'major' %}badge--warn{% endif %}">{{ issue.severity }}</span>
    <span class="badge {% if issue.status == 'open' %}badge--warn{% elif issue.status == 'closed' %}badge--ok{% endif %}">{{ issue.status }}</span>
    <span class="badge">{{ issue.source }}</span>
    <span class="badge">{{ issue.issue_type.code }} {{ issue.issue_type.name }}</span>
    {% if issue.status in ['open', 'acknowledged'] and issue.issue_type.is_blocking %}<span class="badge badge--danger">阻断中</span>{% endif %}
  </p>
  {% if issue.description %}<p>{{ issue.description | issue_linkify }}</p>{% endif %}
</div>

<div class="card">
  <div class="card__title">关联上下文</div>
  <p>SN: {% if issue.serial_unit_id %}<a href="/trace/query?sn={{ issue.serial_unit_id }}">#{{ issue.serial_unit_id }}</a>{% else %}—{% endif %} ·
  WO: {% if issue.work_order_id %}#{{ issue.work_order_id }}{% else %}—{% endif %} ·
  工位: {% if issue.work_station_id %}#{{ issue.work_station_id }}{% else %}—{% endif %} ·
  工序: {% if issue.operation_id %}#{{ issue.operation_id }}{% else %}—{% endif %}
  {% if issue.defect_id %}· <a href="/quality/defects/{{ issue.defect_id }}">关联缺陷 #{{ issue.defect_id }}</a>{% endif %}</p>
  <p style="color:var(--text-soft);font-size:12px">上报 #{{ issue.reported_by_id }} @ {{ issue.reported_at }}
    {% if issue.acknowledged_at %} · 确认 #{{ issue.acknowledged_by_id }} @ {{ issue.acknowledged_at }}{% endif %}
    {% if issue.resolved_at %} · 处置 #{{ issue.resolved_by_id }} @ {{ issue.resolved_at }}{% endif %}
    {% if issue.closed_at %} · 关闭 #{{ issue.closed_by_id }} @ {{ issue.closed_at }}{% endif %}</p>
</div>

{% if is_privileged %}
<div class="card">
  <div class="card__title">状态机操作</div>
  {% if issue.status == 'open' %}
  <form method="post" action="/issues/{{ issue.id }}/acknowledge" style="display:inline">
    <button type="submit">确认 (Acknowledge)</button>
  </form>
  {% elif issue.status == 'acknowledged' %}
  <details>
    <summary><button type="button">处置 (Resolve)</button></summary>
    <form method="post" action="/issues/{{ issue.id }}/resolve" style="margin-top:12px">
      <div class="field"><label>根因 (root_cause)</label><input name="root_cause" required></div>
      <div class="field"><label>遏制措施 (containment_action)</label><input name="containment_action" required></div>
      <div class="field"><label>处理决策 (disposition)</label>
        <select name="disposition" required>
          <option value="">请选择</option>
          <option value="use_as_is">让步接收 (use_as_is)</option>
          <option value="rework">返工 (rework)</option>
          <option value="scrap">报废 (scrap)</option>
          <option value="hold">暂存 (hold)</option>
        </select>
      </div>
      <div class="field" style="flex:1"><label>处置备注</label><input name="resolution_notes"></div>
      <button type="submit">提交处置</button>
    </form>
  </details>
  {% elif issue.status == 'resolved' %}
  <form method="post" action="/issues/{{ issue.id }}/close" style="display:inline">
    <button type="submit">关闭 (Close)</button>
  </form>
  {% elif issue.status == 'closed' %}
  <details>
    <summary><button type="button">重开 (Reopen)</button></summary>
    <form method="post" action="/issues/{{ issue.id }}/reopen" style="margin-top:12px">
      <div class="field" style="flex:1"><label>重开原因</label><input name="reason" required></div>
      <button type="submit">提交重开</button>
    </form>
  </details>
  {% endif %}
</div>
{% endif %}

{% if issue.resolved_at %}
<div class="card">
  <div class="card__title">处置详情</div>
  <p><strong>根因:</strong> {{ issue.root_cause | issue_linkify }}</p>
  <p><strong>遏制:</strong> {{ issue.containment_action | issue_linkify }}</p>
  <p><strong>决策:</strong> <span class="badge">{{ issue.disposition }}</span></p>
  {% if issue.resolution_notes %}<p><strong>备注:</strong> {{ issue.resolution_notes | issue_linkify }}</p>{% endif %}
</div>
{% endif %}

<div class="card">
  <div class="card__title">CAPA 行动 ({{ actions|length }})</div>
  {% if actions %}
  <table class="data-table">
    <thead><tr><th>类型</th><th>标题</th><th>状态</th><th>负责人</th><th>截止</th>{% if is_privileged %}<th>操作</th>{% endif %}</tr></thead>
    <tbody>
      {% for a in actions %}
      <tr>
        <td><span class="badge">{{ a.type }}</span></td>
        <td>{{ a.title | issue_linkify }}</td>
        <td><span class="badge {% if a.status == 'verified' %}badge--ok{% elif a.status == 'open' %}badge--warn{% endif %}">{{ a.status }}</span></td>
        <td>{% if a.assigned_to_id %}#{{ a.assigned_to_id }}{% else %}—{% endif %}</td>
        <td>{{ a.due_date or '—' }}</td>
        {% if is_privileged %}<td>
          {% if a.status == 'open' %}
          <form method="post" action="/issues/actions/{{ a.id }}/start" style="display:inline"><button type="submit" class="btn-secondary">开始</button></form>
          {% elif a.status == 'in_progress' %}
          <form method="post" action="/issues/actions/{{ a.id }}/complete" style="display:inline"><button type="submit" class="btn-secondary">完成</button></form>
          {% elif a.status == 'done' %}
          <form method="post" action="/issues/actions/{{ a.id }}/verify" style="display:inline"><button type="submit" class="btn-secondary">验证</button></form>
          {% endif %}
        </td>{% endif %}
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}<p style="color:var(--text-soft)">暂无 CAPA 行动</p>{% endif %}

  {% if is_privileged and issue.status != 'closed' %}
  <details style="margin-top:12px">
    <summary><button type="button" class="btn-secondary">+ 添加 CAPA</button></summary>
    <form method="post" action="/issues/{{ issue.id }}/actions" style="margin-top:12px">
      <div class="field"><label>类型</label>
        <select name="type" required>
          <option value="corrective">纠正 (corrective)</option>
          <option value="preventive">预防 (preventive)</option>
          <option value="containment">遏制 (containment)</option>
        </select>
      </div>
      <div class="field" style="flex:1"><label>标题</label><input name="title" required></div>
      <div class="field" style="flex:1"><label>描述</label><input name="description"></div>
      <div class="field"><label>负责人 ID</label><input name="assigned_to_id" type="number"></div>
      <div class="field"><label>截止日期</label><input name="due_date" type="date"></div>
      <button type="submit">添加</button>
    </form>
  </details>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: 注册 router 到 main.py**

在 `src/lightmes/main.py` 现有 `from lightmes.modules import (...)` 加 `issue`：

```python
from lightmes.modules import (
    agent_gateway,
    api_v1,
    auth,
    connectivity,
    integration,
    issue,  # 新增
    masterdata,
    production,
    trace,
    quality,
)
```

然后在 `auth.register(app)` 之后、`api_v1.register(app)` 之前加：

```python
issue.register(app)
```

并在 `src/lightmes/modules/issue/__init__.py` 中加：

```python
from lightmes.modules.issue import router


def register(app):
    app.include_router(router.router)
```

- [ ] **Step 5: 写 router 测试**

`tests/modules/issue/test_router.py`：

```python
import pytest
from fastapi.testclient import TestClient

from lightmes.modules.issue.models import Issue, IssueType


@pytest.fixture
def privileged_client(client, db_session, sample_user):
    """sample_user 是 admin（见 conftest），直接用现有 client。"""
    sample_user.role = "admin"
    db_session.flush()
    return client


def test_issue_list_requires_login(client):
    r = client.get("/issues", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_issue_list_visible_to_admin(privileged_client):
    r = privileged_client.get("/issues")
    assert r.status_code == 200
    assert "Issue 看板" in r.text


def test_issue_detail_404(privileged_client):
    r = privileged_client.get("/issues/99999")
    assert r.status_code == 404


def test_issue_close_rejected_with_unverified_capa(
        privileged_client, db_session, sample_user):
    """close 时 CAPA 未全 verified 返回错误。"""
    it = IssueType(code="T", name="T", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  status="resolved", reported_by_id=sample_user.id)
    db_session.add(issue); db_session.flush()
    from lightmes.modules.issue.models import IssueAction
    db_session.add(IssueAction(issue_id=issue.id, type="corrective",
                               title="a", status="open"))
    db_session.commit()
    r = privileged_client.post(f"/issues/{issue.id}/close")
    assert r.status_code == 422
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/modules/issue/test_router.py -v`
Expected: 4 PASS

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/issue/router.py \
        src/lightmes/modules/issue/__init__.py \
        src/lightmes/templates/issue/list.html \
        src/lightmes/templates/issue/detail.html \
        src/lightmes/main.py \
        tests/modules/issue/test_router.py
git commit -m "feat(issue): list/detail HTML routes + lifecycle endpoints"
```

---

### Task 6: Router — CAPA 端点 + IssueType 字典

**Files:**
- Modify: `src/lightmes/modules/issue/router.py`（加 CAPA 转换 + type CRUD）
- Create: `src/lightmes/templates/issue/types.html`
- Test: `tests/modules/issue/test_router.py`（扩展）

**Interfaces:**
- Consumes: Task 3 service `add_action` / `start_action` / `complete_action` / `verify_action` + IssueTypeRepository
- Produces: POST `/issues/{id}/actions` / `/issues/actions/{aid}/(start|complete|verify)` + GET/POST `/issues/types`

- [ ] **Step 1: 加 CAPA 端点到 router.py**

在 `src/lightmes/modules/issue/router.py` 末尾追加：

```python
# ---- CAPA POST 端点 ----

@router.post("/issues/{issue_id}/actions")
def issue_add_action(
    request: Request, issue_id: int,
    type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    assigned_to_id: int | None = Form(None),
    due_date: str = Form(""),
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    from datetime import date as date_t
    try:
        IssueService(db).add_action(
            issue_id,
            type=type, title=title,
            description=description or None,
            assigned_to_id=assigned_to_id,
            due_date=date_t.fromisoformat(due_date) if due_date else None,
        )
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


def _capa_transition(action_id: int, op: str, user, db: Session) -> Response:
    svc = IssueService(db)
    try:
        if op == "start":
            action = svc.start_action(action_id, user.id)
        elif op == "complete":
            action = svc.complete_action(action_id, user.id)
        elif op == "verify":
            action = svc.verify_action(action_id, user.id)
        else:
            return Response(status_code=404)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{action.issue_id}"})


@router.post("/issues/actions/{action_id}/start")
def capa_start(
    action_id: int,
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    return _capa_transition(action_id, "start", user, db)


@router.post("/issues/actions/{action_id}/complete")
def capa_complete(
    action_id: int,
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    return _capa_transition(action_id, "complete", user, db)


@router.post("/issues/actions/{action_id}/verify")
def capa_verify(
    action_id: int,
    user = Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    return _capa_transition(action_id, "verify", user, db)


# ---- IssueType 字典 CRUD ----

@router.get("/issues/types", response_class=HTMLResponse)
def issue_types_page(
    request: Request,
    user = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    types = IssueTypeRepository(db).list_all()
    return templates.TemplateResponse(
        request, "issue/types.html", {"types": types})


@router.post("/issues/types")
def issue_types_create(
    code: str = Form(...),
    name: str = Form(...),
    severity: str = Form(...),
    is_blocking: bool = Form(False),
    is_active: bool = Form(False),
    description: str = Form(""),
    user = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    from lightmes.modules.issue.models import IssueType
    try:
        db.add(IssueType(
            code=code.strip(), name=name.strip(), severity=severity,
            is_blocking=is_blocking, is_active=is_active,
            description=description or None))
        db.commit()
    except Exception as e:
        db.rollback()
        return Response(status_code=400, content=str(e))
    return Response(status_code=303, headers={"Location": "/issues/types"})


@router.post("/issues/types/{type_id}/toggle-active")
def issue_types_toggle(
    type_id: int,
    user = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    t = db.get(IssueType, type_id)
    if t is None:
        return Response(status_code=404, content="不存在")
    t.is_active = not t.is_active
    db.commit()
    return Response(status_code=303, headers={"Location": "/issues/types"})
```

- [ ] **Step 2: 写 types.html**

`src/lightmes/templates/issue/types.html`：

```html
{% extends "base.html" %}
{% block title %}Issue 类型字典{% endblock %}
{% block content %}
<h1 class="page-title">Issue 类型字典 <small>Admin only</small></h1>

<div class="card">
  <div class="card__title">新建类型</div>
  <form method="post" action="/issues/types" class="form-row">
    <div class="field"><label>code</label><input name="code" required></div>
    <div class="field"><label>name</label><input name="name" required></div>
    <div class="field"><label>severity</label>
      <select name="severity">
        {% for s in ['info', 'minor', 'major', 'critical'] %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
      </select>
    </div>
    <div class="field"><label>is_blocking</label><input type="checkbox" name="is_blocking" value="true"></div>
    <div class="field"><label>is_active</label><input type="checkbox" name="is_active" value="true" checked></div>
    <div class="field" style="flex:1"><label>description</label><input name="description"></div>
    <button type="submit">创建</button>
  </form>
</div>

<div class="card">
  <div class="card__title">现有类型 ({{ types|length }})</div>
  <table class="data-table">
    <thead><tr><th>code</th><th>name</th><th>severity</th><th>blocking</th><th>active</th><th>操作</th></tr></thead>
    <tbody>
      {% for t in types %}
      <tr>
        <td><code>{{ t.code }}</code></td>
        <td>{{ t.name }}</td>
        <td><span class="badge">{{ t.severity }}</span></td>
        <td>{{ "✓" if t.is_blocking else "—" }}</td>
        <td>{{ "✓" if t.is_active else "—" }}</td>
        <td>
          <form method="post" action="/issues/types/{{ t.id }}/toggle-active" style="display:inline">
            <button type="submit" class="btn-secondary">停用/启用</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 3: 加 router 测试**

在 `tests/modules/issue/test_router.py` 追加：

```python
def test_add_capa_creates_action(privileged_client, db_session, sample_user):
    from lightmes.modules.issue.models import Issue, IssueType
    it = IssueType(code="T", name="T", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=sample_user.id)
    db_session.add(issue); db_session.commit()
    r = privileged_client.post(f"/issues/{issue.id}/actions",
                               data={"type": "corrective", "title": "act"})
    assert r.status_code == 303
    from lightmes.modules.issue.models import IssueAction
    actions = db_session.query(IssueAction).all()
    assert len(actions) == 1
    assert actions[0].title == "act"


def test_capa_lifecycle_via_http(privileged_client, db_session, sample_user):
    from lightmes.modules.issue.models import Issue, IssueAction, IssueType
    it = IssueType(code="T2", name="T2", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=sample_user.id)
    db_session.add(issue); db_session.commit()
    privileged_client.post(f"/issues/{issue.id}/actions",
                           data={"type": "corrective", "title": "a"})
    a = db_session.query(IssueAction).first()
    assert privileged_client.post(
        f"/issues/actions/{a.id}/start").status_code == 303
    assert privileged_client.post(
        f"/issues/actions/{a.id}/complete").status_code == 303
    assert privileged_client.post(
        f"/issues/actions/{a.id}/verify").status_code == 303
    db_session.refresh(a)
    assert a.status == "verified"


def test_types_page_admin_only(privileged_client, client):
    assert privileged_client.get("/issues/types").status_code == 200
    # 未登录被重定向
    r = client.get("/issues/types", follow_redirects=False)
    assert r.status_code in (302, 401, 403)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/modules/issue/test_router.py -v`
Expected: 7 PASS（原 4 + 新 3）

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/issue/router.py \
        src/lightmes/templates/issue/types.html \
        tests/modules/issue/test_router.py
git commit -m "feat(issue): CAPA endpoints + IssueType admin CRUD"
```

---

### Task 7: Station 集成 — SN 阻断 + ANDON 表单

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（注入阻断检查）
- Modify: `src/lightmes/modules/production/station_service.py`（`view` 增加 `blocking_issue` 字段）
- Modify: `src/lightmes/modules/production/router.py`（加 `/production/station/andon-form` GET）
- Modify: `src/lightmes/modules/production/schemas.py`（StationView 加 `blocking_issue`）
- Create: `src/lightmes/templates/production/partials/andon_form.html`
- Modify: `src/lightmes/templates/production/station_view.html`（横幅 + ANDON 启用）
- Modify: `src/lightmes/templates/production/station.html`（modal 容器 + ANDON 按钮）
- Test: `tests/modules/issue/test_station_integration.py`

**Interfaces:**
- Consumes: Task 3 `IssueService.check_block_for_sn`
- Produces: SN pass 被阻断时 raise BusinessRuleError + station view 显示阻断横幅 + ANDON 按钮可建 issue

- [ ] **Step 1: 注入阻断检查到 pass_operation**

在 `src/lightmes/modules/production/operation_pass_service.py` 的 `pass_operation` 方法中，定位 SN 之后、工单状态检查之前（约 47 行后）插入：

```python
        # 1.5. SN 级阻断检查（Issue/Andon）
        if su is not None:
            from lightmes.modules.issue.service import IssueService
            blocking = IssueService(self.db).check_block_for_sn(su.id)
            if blocking is not None:
                raise BusinessRuleError(
                    f"该 SN 被 Issue #{blocking.id} 阻断："
                    f"[{blocking.severity.upper()}] {blocking.title}。"
                    f"请等待主管处置或访问 /issues/{blocking.id}"
                )
```

（注意缩进对齐方法体；放在 `if su.status in (...)` 之后）

- [ ] **Step 2: 写 station 阻断集成测试**

`tests/modules/issue/test_station_integration.py`：

```python
import pytest
from lightmes.modules.issue.models import Issue, IssueType
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.schemas import OperationPassInput
from lightmes.shared.errors import BusinessRuleError


@pytest.fixture
def blocking_type(db_session):
    t = IssueType(code="T_block_int", name="b", severity="critical", is_blocking=True)
    db_session.add(t); db_session.flush()
    return t


def test_pass_blocked_when_sn_has_open_blocking_issue(
        db_session, blocking_type, sample_user, full_station_setup):
    """已 setup 一个 SN + 工单 + 工艺 + 工位；创建 blocking issue 后 pass 应失败。"""
    su = full_station_setup.serial_unit
    db_session.add(Issue(
        issue_type_id=blocking_type.id, title="bad", severity="critical",
        source="manual", serial_unit_id=su.id, status="open",
        reported_by_id=sample_user.id))
    db_session.flush()

    svc = OperationPassService(db_session)
    data = OperationPassInput(
        sn=su.sn, work_station_id=full_station_setup.work_station_id,
        operator_id=sample_user.id, components=[], params=[])
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(data)
    assert "Issue #" in str(exc.value)


def test_pass_allowed_after_blocking_resolved(
        db_session, blocking_type, sample_user, full_station_setup):
    """resolved 状态的 blocking issue 不阻断。"""
    su = full_station_setup.serial_unit
    db_session.add(Issue(
        issue_type_id=blocking_type.id, title="bad", severity="critical",
        source="manual", serial_unit_id=su.id, status="resolved",
        reported_by_id=sample_user.id))
    db_session.flush()
    svc = OperationPassService(db_session)
    # 不应 raise IssueBlockError —— 可能因其他业务规则失败，但不是阻断
    try:
        svc.pass_operation(OperationPassInput(
            sn=su.sn, work_station_id=full_station_setup.work_station_id,
            operator_id=sample_user.id, components=[], params=[]))
    except BusinessRuleError as e:
        assert "Issue #" not in str(e)
```

注意：`full_station_setup` fixture 需在 conftest.py 添加（见 Step 3）。如已存在可跳过。

- [ ] **Step 3: 在 conftest.py 加 full_station_setup fixture（如缺失）**

在 `tests/conftest.py` 末尾追加（如已有类似 fixture 复用即可）：

```python
@pytest.fixture
def full_station_setup(db_session, sample_user):
    """提供完整的过站上下文：Product + Routing + Operation + Line + WorkStation + WorkOrder + SerialUnit。"""
    from dataclasses import dataclass
    from lightmes.modules.masterdata.models import (
        Product, Routing, Operation, Line, WorkStation,
    )
    from lightmes.modules.production.models import WorkOrder, SerialUnit

    product = Product(code="P1", name="P1", type="finished")
    db_session.add(product); db_session.flush()
    line = Line(code="L1", name="L1")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS1", name="WS1", line_id=line.id)
    db_session.add(ws); db_session.flush()
    routing = Routing(code="R1", name="R1", product_id=product.id, status="active")
    db_session.add(routing); db_session.flush()
    op = Operation(seq=10, code="OP10", name="OP10", routing_id=routing.id,
                   default_work_station_id=ws.id)
    db_session.add(op); db_session.flush()
    wo = WorkOrder(code="WO1", product_id=product.id, routing_id=routing.id,
                   line_id=line.id, qty=10, status="released")
    db_session.add(wo); db_session.flush()
    su = SerialUnit(sn="SN_TEST_001", work_order_id=wo.id,
                    current_operation_seq=0, status="in_process")
    db_session.add(su); db_session.flush()

    @dataclass
    class Setup:
        product: Product
        line: Line
        work_station: WorkStation
        work_station_id: int
        routing: Routing
        operation: Operation
        work_order: WorkOrder
        serial_unit: SerialUnit

    return Setup(product, line, ws, ws.id, routing, op, wo, su)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/modules/issue/test_station_integration.py -v`
Expected: 2 PASS

- [ ] **Step 5: 加 ANDON form GET 端点**

在 `src/lightmes/modules/production/router.py` 中找一个合适位置（其他 station 子路径附近）追加：

```python
@router.get("/production/station/andon-form", response_class=HTMLResponse)
def station_andon_form(
    request: Request,
    work_station_id: int = 0,
    serial_unit_id: int = 0,
    work_order_id: int = 0,
    operation_id: int = 0,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})
    from lightmes.modules.issue.repository import IssueTypeRepository
    types = IssueTypeRepository(db).list_active()
    return templates.TemplateResponse(
        request, "production/partials/andon_form.html",
        {"types": types,
         "ctx": {"work_station_id": work_station_id,
                 "serial_unit_id": serial_unit_id,
                 "work_order_id": work_order_id,
                 "operation_id": operation_id}})
```

- [ ] **Step 6: 写 andon_form.html**

`src/lightmes/templates/production/partials/andon_form.html`：

```html
<div class="skip-form">
  <h3>异常呼叫 (ANDON)</h3>
  <form method="post" action="/issues" hx-post="/issues" hx-target="#station-root" hx-swap="innerHTML"
        onsubmit="setTimeout(()=>document.getElementById('andon-modal').style.display='none',100)">
    <input type="hidden" name="source" value="station_andon">
    <input type="hidden" name="work_station_id" value="{{ ctx.work_station_id }}">
    <input type="hidden" name="serial_unit_id" value="{{ ctx.serial_unit_id }}">
    <input type="hidden" name="work_order_id" value="{{ ctx.work_order_id }}">
    <input type="hidden" name="operation_id" value="{{ ctx.operation_id }}">
    <div class="field"><label>类型</label>
      <select name="issue_type_id" required>
        {% for t in types %}
        <option value="{{ t.id }}">[{{ t.severity }}] {{ t.name }}{{ " · 阻断" if t.is_blocking else "" }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="field" style="flex:1"><label>标题</label><input name="title" required placeholder="一句话简述"></div>
    <div class="field" style="flex:1"><label>描述</label><input name="description"></div>
    <div class="form-actions">
      <button type="submit">上报</button>
      <button type="button" class="btn-secondary" onclick="document.getElementById('andon-modal').style.display='none'">取消</button>
    </div>
  </form>
</div>
```

- [ ] **Step 7: 加 POST /issues 端点到 issue router（如果未在 Task 5 加）**

检查 `src/lightmes/modules/issue/router.py` 是否已有 `@router.post("/issues")`。如未加，在 list 端点之后追加：

```python
@router.post("/issues")
def issue_create(
    request: Request,
    issue_type_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    source: str = Form("manual"),
    work_station_id: int | None = Form(None),
    serial_unit_id: int | None = Form(None),
    work_order_id: int | None = Form(None),
    operation_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})
    try:
        issue = IssueService(db).create_issue(
            issue_type_id=issue_type_id,
            title=title,
            description=description or None,
            source=source,
            work_station_id=work_station_id or None,
            serial_unit_id=serial_unit_id or None,
            work_order_id=work_order_id or None,
            operation_id=operation_id or None,
            reported_by_id=user.id)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue.id}"})
```

注意：ANDON 表单用 htmx 提交，target 是 `#station-root` —— 但 `/issues` POST 默认 303 重定向。这个矛盾要解决：ANDON 提交后应触发 station view 刷新（不跳转 /issues/{id}）。改为返回 JS 触发 htmx 重新加载 station view。

修正版本（替换上面 `return Response(status_code=303, ...)` 这行）：

```python
    # ANDON 提交后留在 station 页：返回小段 JS 触发 station view 刷新
    if source == "station_andon":
        return HTMLResponse(
            f"<script>htmx.trigger(document.getElementById('station-enter-form'), 'submit'); "
            f"window.showErrorModal('Issue #{issue.id} 已上报');</script>")
    return Response(status_code=303, headers={"Location": f"/issues/{issue.id}"})
```

- [ ] **Step 8: 改 station_view.html 加阻断横幅 + ANDON 按钮启用**

在 `src/lightmes/templates/production/station_view.html` 的 `<div class="station">` 之后立即插入：

```html
{% if view.blocking_issue %}
<div class="alert alert--danger" style="margin-bottom:6px">
  ⛔ Issue #{{ view.blocking_issue.id }} 阻断中：
  [{{ view.blocking_issue.severity|upper }}] {{ view.blocking_issue.title }}
  · 状态: {{ view.blocking_issue.status }}
  · <a href="/issues/{{ view.blocking_issue.id }}">查看详情 →</a>
</div>
{% endif %}
```

把现有 disabled ANDON 按钮：

```html
<button type="button" class="btn-secondary" disabled title="暂未开放">异常呼叫 (ANDON)</button>
```

替换为：

```html
<button type="button" class="btn-secondary"
        hx-get="/production/station/andon-form"
        hx-vals='{"work_station_id": "{{ work_station_id }}", "serial_unit_id": "{{ view.sn_id or "" }}", "work_order_id": "{{ view.work_order_id or "" }}", "operation_id": "{{ view.current_op.id if view.current_op else "" }}'
        hx-target="#andon-modal-body"
        onclick="document.getElementById('andon-modal').style.display='flex'">
  异常呼叫 (ANDON)
</button>
```

- [ ] **Step 9: 在 station.html 加 modal 容器**

在 `src/lightmes/templates/production/station.html` 的 `<div id="station-root">` 之后追加：

```html
<div class="modal" id="andon-modal" style="display:none">
  <div class="modal__body"><div id="andon-modal-body"></div></div>
</div>
```

- [ ] **Step 10: station_service 加 blocking_issue 字段**

在 `src/lightmes/modules/production/station_service.py` 的 `load` 方法返回 StationView 之前，查 blocking issue：

```python
        # Issue 阻断横幅
        blocking_issue = None
        if su is not None:
            from lightmes.modules.issue.service import IssueService
            blocking_issue = IssueService(self.db).check_block_for_sn(su.id)
```

把 `blocking_issue` 加入 StationView 构造参数；并在 `schemas.py` 的 `StationView` 加字段：

```python
    blocking_issue: Any | None = None  # Issue 模型或 None
```

`Any` 来自 typing。

- [ ] **Step 11: Run 全部 issue tests**

Run: `uv run pytest tests/modules/issue/ -v`
Expected: 全部 PASS（含 station 集成）

- [ ] **Step 12: Commit**

```bash
git add src/lightmes/modules/production/operation_pass_service.py \
        src/lightmes/modules/production/station_service.py \
        src/lightmes/modules/production/router.py \
        src/lightmes/modules/production/schemas.py \
        src/lightmes/modules/issue/router.py \
        src/lightmes/templates/production/partials/andon_form.html \
        src/lightmes/templates/production/station_view.html \
        src/lightmes/templates/production/station.html \
        tests/modules/issue/test_station_integration.py \
        tests/conftest.py
git commit -m "feat(issue): station SN blocking + ANDON button + block banner"
```

---

### Task 8: Defect 联动

**Files:**
- Modify: `src/lightmes/modules/production/defect_service.py`（`log_defect` 加 `create_issue=False` 参数）
- Modify: `src/lightmes/modules/quality/router.py`（透传 form 字段 `create_issue`）
- Modify: `src/lightmes/templates/quality/defect_log.html`（加 checkbox）
- Test: `tests/modules/issue/test_defect_linkage.py`

**Interfaces:**
- Consumes: Task 3 `IssueService.create_from_defect`
- Produces: defect 登记表单可选「同时上报 Issue」

- [ ] **Step 1: 写 defect linkage 测试**

`tests/modules/issue/test_defect_linkage.py`：

```python
import pytest
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.issue.models import Issue


def test_log_defect_with_create_issue_true_links(
        db_session, sample_user, full_station_setup):
    """log_defect(create_issue=True) 同时建 issue + defect_id 关联。"""
    from lightmes.modules.production.models import DefectType
    dt = DefectType(code="DT", name="DT", category="质量", severity="major", is_active=True)
    db_session.add(dt); db_session.flush()
    su = full_station_setup.serial_unit

    svc = DefectService(db_session)
    defect = svc.log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=sample_user.id,
        create_issue=True)
    db_session.flush()

    issue = db_session.query(Issue).filter(Issue.defect_id == defect.id).one_or_none()
    assert issue is not None
    assert issue.source == "defect_linked"
    assert issue.serial_unit_id == su.id
    assert issue.severity == "major"


def test_log_defect_without_create_issue_no_link(
        db_session, sample_user, full_station_setup):
    """默认 create_issue=False 不联动。"""
    from lightmes.modules.production.models import DefectType, DefectRecord
    dt = DefectType(code="DT2", name="DT2", category="质量", severity="major", is_active=True)
    db_session.add(dt); db_session.flush()
    su = full_station_setup.serial_unit

    svc = DefectService(db_session)
    defect = svc.log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=sample_user.id)
    db_session.flush()

    issues = db_session.query(Issue).filter(Issue.defect_id == defect.id).all()
    assert len(issues) == 0
```

- [ ] **Step 2: 修改 defect_service.py 加参数**

在 `src/lightmes/modules/production/defect_service.py` 的 `log_defect` 方法签名加 `create_issue: bool = False`，并在 commit 之前插入联动逻辑：

签名变为：

```python
    def log_defect(self, defect_type_id: int, sn: str, discovered_by: int,
                   operation_id: int | None = None, work_station_id: int | None = None,
                   position: str | None = None, remark: str | None = None,
                   create_issue: bool = False,
                   ) -> DefectRecord:
```

方法末尾 `return defect` 之前插入：

```python
        if create_issue:
            from lightmes.modules.issue.service import IssueService
            IssueService(self.db).create_from_defect(
                defect, reported_by_id=discovered_by)
```

- [ ] **Step 3: 修改 quality router 透传 form 字段**

在 `src/lightmes/modules/quality/router.py` 第 611 行附近的 `defect_log_submit` 函数加：

```python
    create_issue: bool = Form(False),
```

签名变为：

```python
@router.post("/quality/defects/log", response_class=HTMLResponse)
def defect_log_submit(
    request: Request,
    sn: str = Form(...),
    defect_type_id: int = Form(...),
    position: str = Form(""),
    remark: str = Form(""),
    create_issue: bool = Form(False),
    db: Session = Depends(get_db),
) -> HTMLResponse:
```

调用 `log_defect` 透传：

```python
        record = DefectService(db).log_defect(
            defect_type_id=defect_type_id, sn=sn, discovered_by=user.id,
            position=position if position else None,
            remark=remark if remark else None,
            create_issue=create_issue)
```

注意：HTML checkbox 不传时缺值，FastAPI `Form(False)` 默认值会生效；checkbox 勾选提交 `"true"` 字符串，FastAPI 会自动转 bool。

- [ ] **Step 4: 修改 defect_log.html 加 checkbox**

在 `src/lightmes/templates/quality/defect_log.html` 的「备注」字段后、`<button>` 之前加：

```html
    <div class="field"><label>同时上报 Issue (Andon)</label>
      <input type="checkbox" name="create_issue" value="true"></div>
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/modules/issue/test_defect_linkage.py -v`
Expected: 2 PASS

- [ ] **Step 6: 回归现有 defect 测试**

Run: `uv run pytest tests/modules/ -v`
Expected: 全部 PASS（log_defect 签名向后兼容）

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/production/defect_service.py \
        src/lightmes/modules/quality/router.py \
        src/lightmes/templates/quality/defect_log.html \
        tests/modules/issue/test_defect_linkage.py
git commit -m "feat(issue): defect -> issue linkage via log_defect(create_issue=True)"
```

---

### Task 9: 首页卡片 + 顶栏链接

**Files:**
- Modify: `src/lightmes/main.py`（home route 加 open_count/blocking_count）
- Modify: `src/lightmes/templates/home.html`（加异常管理卡片）
- Modify: `src/lightmes/templates/base.html`（顶栏加链接）

**Interfaces:**
- Consumes: Task 2 `IssueRepository.count_open` / `count_blocking`
- Produces: home dashboard 显示 + 顶栏入口

- [ ] **Step 1: 改 home route**

在 `src/lightmes/main.py` 的 `def home(...)` 函数中，return 之前加：

```python
    from lightmes.modules.issue.repository import IssueRepository
    issue_repo = IssueRepository(db)
    open_count = issue_repo.count_open()
    blocking_count = issue_repo.count_blocking()
```

修改 return：

```python
    return _templates.TemplateResponse(request, "home.html", {
        "user": user,
        "issue_open_count": open_count,
        "issue_blocking_count": blocking_count,
    })
```

- [ ] **Step 2: 加首页卡片**

在 `src/lightmes/templates/home.html` 的现有 `<div class="home-grid">` 内追加第 5 张卡片（在「质量管理」之后）：

```html
<div class="card">
  <div class="card__title">异常管理</div>
  <div class="nav-grid">
    <a class="nav-card" href="/issues">
      <span class="nav-card__icon">🚨</span>
      <div class="nav-card__name">Issue 看板</div>
      <div class="nav-card__desc">未关闭: <strong>{{ issue_open_count }}</strong>{% if issue_blocking_count %} <span class="badge badge--danger">阻断 {{ issue_blocking_count }}</span>{% endif %}</div>
    </a>
  </div>
</div>
```

注意：`home-grid` 现是 `grid-template-columns: 2fr 1fr 1fr`（3 列）。改成 4 列：

修改 `src/lightmes/static/css/app.css` 的 `.home-grid`：

```css
.home-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 1fr;
  gap: 14px;
  align-items: start;
}
```

（仅 2fr → 2fr 1fr 1fr 1fr 一处改动）

- [ ] **Step 3: 加顶栏链接**

在 `src/lightmes/templates/base.html` 的「数采看板」之后、「退出」之前加：

```html
    <a class="app-bar__link" href="/issues">异常</a>
```

- [ ] **Step 4: 手测**

启动应用：`uv run uvicorn lightmes.main:app --port 8000 --reload`

浏览器访问 `/`，验证：
- 顶栏出现「异常」链接
- 首页出现「异常管理」卡片，未关闭数显示
- 点击「Issue 看板」跳 `/issues`

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/main.py src/lightmes/templates/home.html \
        src/lightmes/templates/base.html src/lightmes/static/css/app.css
git commit -m "feat(issue): home dashboard card + top bar entry"
```

---

### Task 10: MCP Agent 工具

**Files:**
- Create: `src/lightmes/modules/agent_gateway/tools/issues.py`
- Modify: `src/lightmes/modules/agent_gateway/schemas.py`（加 Issue 相关 schema）
- Modify: `src/lightmes/modules/agent_gateway/tools/__init__.py`（触发 import 注册）
- Test: `tests/modules/issue/test_mcp_tools.py`

**Interfaces:**
- Consumes: Task 3 IssueService
- Produces: 4 个 MCP 工具：`list_issues` / `get_issue` / `create_issue` / `update_issue_status`

- [ ] **Step 1: 加 schemas**

在 `src/lightmes/modules/agent_gateway/schemas.py` 末尾追加：

```python
class IssueActionReadV1(BaseModel):
    id: int
    type: str
    title: str
    status: str
    assigned_to_id: int | None = None
    due_date: str | None = None


class IssueReadV1(BaseModel):
    id: int
    issue_type_code: str
    title: str
    description: str | None
    status: str
    severity: str
    source: str
    serial_unit_id: int | None = None
    work_order_id: int | None = None
    work_station_id: int | None = None
    defect_id: int | None = None
    reported_at: str
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    closed_at: str | None = None
    is_blocking: bool = False

    model_config = ConfigDict(from_attributes=True)


class CreateIssueResult(BaseModel):
    id: int
    status: str = "open"


class UpdateIssueStatusResult(BaseModel):
    id: int
    status: str
```

- [ ] **Step 2: 写 MCP 工具**

`src/lightmes/modules/agent_gateway/tools/issues.py`：

```python
"""Issue MCP tools (4: list/get/create/update_status)."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    IssueReadV1, IssueActionReadV1,
    CreateIssueResult, UpdateIssueStatusResult,
)
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_issues(
    statuses: list[str] | None = None,
    severities: list[str] | None = None,
    sources: list[str] | None = None,
    serial_unit_id: int | None = None,
    work_order_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> list[IssueReadV1]:
    """列出 Issue，可按状态/严重度/来源/SN/WO 过滤。

    Args:
        statuses: 可选，如 ["open", "acknowledged"]。
        severities: 可选，如 ["critical", "major"]。
        sources: 可选，如 ["station_andon", "defect_linked", "manual"]。
        serial_unit_id: 可选，按 SN 过滤。
        work_order_id: 可选。
        page: 从 1 开始。
        size: 每页数量。
    """
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.issue.repository import IssueRepository

    db = get_http_request().state.db_session
    rows = IssueRepository(db).list(
        statuses=statuses, severities=severities, sources=sources,
        serial_unit_id=serial_unit_id, work_order_id=work_order_id,
        page=page, size=size)
    return [_to_read(db, r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_issue(issue_id: int) -> dict:
    """按 id 查 issue，含 actions。

    Args:
        issue_id: Issue id。

    Raises:
        NotFoundError: 不存在。
    """
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.issue.repository import (
        IssueActionRepository, IssueRepository,
    )
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    issue = IssueRepository(db).get(issue_id)
    if issue is None:
        raise NotFoundError(f"Issue 不存在: {issue_id}")
    actions = IssueActionRepository(db).list_for_issue(issue_id)
    return {
        "issue": _to_read(db, issue).model_dump(),
        "actions": [IssueActionReadV1(
            id=a.id, type=a.type, title=a.title, status=a.status,
            assigned_to_id=a.assigned_to_id,
            due_date=a.due_date.isoformat() if a.due_date else None,
        ).model_dump() for a in actions],
    }


@mcp.tool()
@require_scope("write")
def create_issue(
    type_code: str,
    title: str,
    description: str | None = None,
    source: str = "manual",
    serial_unit_id: int | None = None,
    work_order_id: int | None = None,
    work_station_id: int | None = None,
) -> CreateIssueResult:
    """创建 Issue（默认 source=manual；如要标 station 上报请传 source=station_andon）。

    Args:
        type_code: IssueType code，如 'quality' / 'material_shortage'。
        title: 简短标题。
        description: 可选详细描述。
        source: station_andon | defect_linked | manual。
        serial_unit_id: 可选。
        work_order_id: 可选。
        work_station_id: 可选。
    """
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.issue.repository import IssueTypeRepository
    from lightmes.modules.issue.service import IssueService
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    request = get_http_request()
    user_id = request.state.user.id

    it = IssueTypeRepository(db).get_by_code(type_code)
    if it is None:
        raise NotFoundError(f"IssueType code 不存在: {type_code}")

    issue = IssueService(db).create_issue(
        issue_type_id=it.id,
        title=title,
        description=description,
        source=source,
        serial_unit_id=serial_unit_id,
        work_order_id=work_order_id,
        work_station_id=work_station_id,
        reported_by_id=user_id,
    )
    db.commit()
    return CreateIssueResult(id=issue.id, status=issue.status)


@mcp.tool()
@require_scope("write")
def update_issue_status(
    issue_id: int,
    action: str,
    root_cause: str | None = None,
    containment_action: str | None = None,
    disposition: str | None = None,
    reopen_reason: str | None = None,
) -> UpdateIssueStatusResult:
    """触发 Issue 状态转换。

    Args:
        issue_id: Issue id。
        action: acknowledge | resolve | close | reopen。
        root_cause / containment_action / disposition: action=resolve 时必填。
        reopen_reason: action=reopen 时必填。

    Raises:
        BusinessRuleError: 当前状态不允许该转换。
        ValidationError: 缺必填字段。
    """
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.issue.service import IssueService

    db = get_http_request().state.db_session
    request = get_http_request()
    user_id = request.state.user.id

    svc = IssueService(db)
    if action == "acknowledge":
        issue = svc.acknowledge(issue_id, user_id)
    elif action == "resolve":
        if not root_cause or not containment_action or not disposition:
            from lightmes.shared.errors import ValidationError
            raise ValidationError("resolve 需要 root_cause / containment_action / disposition")
        issue = svc.resolve(
            issue_id, user_id,
            root_cause=root_cause,
            containment_action=containment_action,
            disposition=disposition,
        )
    elif action == "close":
        issue = svc.close(issue_id, user_id)
    elif action == "reopen":
        if not reopen_reason:
            from lightmes.shared.errors import ValidationError
            raise ValidationError("reopen 需要 reopen_reason")
        issue = svc.reopen(issue_id, user_id, reason=reopen_reason)
    else:
        from lightmes.shared.errors import ValidationError
        raise ValidationError(f"非法 action: {action}")

    db.commit()
    return UpdateIssueStatusResult(id=issue.id, status=issue.status)


def _to_read(db, issue) -> IssueReadV1:
    """Issue ORM → IssueReadV1（带 is_blocking 派生 + 关联 type code）。"""
    from lightmes.modules.issue.models import IssueType
    from lightmes.modules.issue.service import IssueService
    it = db.get(IssueType, issue.issue_type_id)
    return IssueReadV1(
        id=issue.id,
        issue_type_code=it.code if it else "",
        title=issue.title,
        description=issue.description,
        status=issue.status,
        severity=issue.severity,
        source=issue.source,
        serial_unit_id=issue.serial_unit_id,
        work_order_id=issue.work_order_id,
        work_station_id=issue.work_station_id,
        defect_id=issue.defect_id,
        reported_at=issue.reported_at.isoformat() if issue.reported_at else "",
        acknowledged_at=issue.acknowledged_at.isoformat() if issue.acknowledged_at else None,
        resolved_at=issue.resolved_at.isoformat() if issue.resolved_at else None,
        closed_at=issue.closed_at.isoformat() if issue.closed_at else None,
        is_blocking=IssueService.is_blocking(issue),
    )
```

- [ ] **Step 3: 在 tools/__init__.py 加 import**

修改 `src/lightmes/modules/agent_gateway/tools/__init__.py`，加：

```python
from lightmes.modules.agent_gateway.tools import issues  # noqa: F401
```

- [ ] **Step 4: 写 MCP 工具测试**

`tests/modules/issue/test_mcp_tools.py`：

```python
import pytest
from lightmes.modules.issue.models import Issue, IssueType


@pytest.fixture
def issue_type_quality(db_session):
    # 默认 seed 已有 'quality' type；测试中查或建
    from lightmes.modules.issue.repository import IssueTypeRepository
    t = IssueTypeRepository(db_session).get_by_code("quality")
    if t is None:
        t = IssueType(code="quality", name="Q", severity="major")
        db_session.add(t); db_session.flush()
    return t


def test_list_issues_returns_empty_when_no_data(authenticated_mcp_client):
    r = authenticated_mcp_client.call_tool("list_issues", {})
    assert r == []


def test_create_and_get_issue(authenticated_mcp_client, db_session, issue_type_quality):
    created = authenticated_mcp_client.call_tool(
        "create_issue",
        {"type_code": "quality", "title": "test from mcp"})
    assert created["status"] == "open"
    issue_id = created["id"]

    got = authenticated_mcp_client.call_tool(
        "get_issue", {"issue_id": issue_id})
    assert got["issue"]["id"] == issue_id
    assert got["issue"]["title"] == "test from mcp"
    assert got["actions"] == []


def test_update_status_lifecycle(authenticated_mcp_client, db_session,
                                  issue_type_quality, sample_user):
    """acknowledge → resolve → close 全链。"""
    created = authenticated_mcp_client.call_tool(
        "create_issue", {"type_code": "quality", "title": "lc"})
    issue_id = created["id"]

    authenticated_mcp_client.call_tool(
        "update_issue_status",
        {"issue_id": issue_id, "action": "acknowledge"})
    authenticated_mcp_client.call_tool(
        "update_issue_status",
        {"issue_id": issue_id, "action": "resolve",
         "root_cause": "rc", "containment_action": "ca", "disposition": "rework"})
    authenticated_mcp_client.call_tool(
        "update_issue_status",
        {"issue_id": issue_id, "action": "close"})

    got = authenticated_mcp_client.call_tool(
        "get_issue", {"issue_id": issue_id})
    assert got["issue"]["status"] == "closed"
```

注意：`authenticated_mcp_client` fixture 应已存在于 `tests/conftest.py`（agent_gateway 之前的 task 建过）。如缺失需添加（见 Step 5）。

- [ ] **Step 5: 如缺失 authenticated_mcp_client fixture，在 conftest.py 加**

```python
@pytest.fixture
def authenticated_mcp_client(client, db_session, sample_user):
    """封装 client 调用 MCP 工具的辅助 fixture。"""
    from lightmes.modules.agent_gateway.server import mcp

    class _Wrapper:
        def __init__(self, c):
            self.c = c
        def call_tool(self, name, args):
            # 调 /mcp endpoint 的简化封装；具体取决于现有 mcp 测试模式
            # 这里假设有 /mcp/tools/{name}/call 之类的 HTTP 端点
            r = self.c.post(f"/mcp/tools/{name}/call", json=args)
            assert r.status_code == 200, r.text
            return r.json()
    return _Wrapper(client)
```

**如果项目已有 mcp 工具测试基线**（看 `tests/modules/test_mcp_*` 或类似），按现有模式实现。

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/modules/issue/test_mcp_tools.py -v`
Expected: 3 PASS

- [ ] **Step 7: 验证 tools/list 含新工具**

启动 app：`uv run uvicorn lightmes.main:app --port 8000`

调：
```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer <api_key>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

确认响应中包含 `list_issues` / `get_issue` / `create_issue` / `update_issue_status`。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/agent_gateway/tools/issues.py \
        src/lightmes/modules/agent_gateway/tools/__init__.py \
        src/lightmes/modules/agent_gateway/schemas.py \
        tests/modules/issue/test_mcp_tools.py \
        tests/conftest.py
git commit -m "feat(issue): 4 MCP tools (list/get/create/update_status)"
```

---

### Task 11: 端到端集成测试 + 文档更新

**Files:**
- Create: `tests/modules/issue/test_e2e.py`
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_lightmes.md`（追加 Issue 模块小节）

**Interfaces:**
- Consumes: 全部前述 task
- Produces: 一条完整 happy-path E2E 测试 + 项目记忆更新

- [ ] **Step 1: 写 E2E 测试**

`tests/modules/issue/test_e2e.py`：

```python
"""Issue/Andon 完整 happy-path 端到端。

流程：
1. operator 在 station 创建 blocking issue (ANDON)
2. station pass 被阻断
3. supervisor acknowledge → resolve
4. operator 再次 pass 通过
5. supervisor close
"""
import pytest
from lightmes.modules.issue.models import Issue, IssueType
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.schemas import OperationPassInput
from lightmes.modules.issue.service import IssueService
from lightmes.shared.errors import BusinessRuleError


def test_e2e_blocking_lifecycle(
        db_session, sample_user, full_station_setup):
    su = full_station_setup.serial_unit
    ws_id = full_station_setup.work_station_id

    # 1. operator 建 blocking issue
    t = IssueType(code="E2E_BLOCK", name="b", severity="critical", is_blocking=True)
    db_session.add(t); db_session.flush()
    svc = IssueService(db_session)
    issue = svc.create_issue(
        issue_type_id=t.id, title="设备卡死",
        source="station_andon", serial_unit_id=su.id,
        work_station_id=ws_id, reported_by_id=sample_user.id)
    db_session.flush()
    assert svc.is_blocking(issue) is True

    # 2. pass 被阻断
    op_svc = OperationPassService(db_session)
    with pytest.raises(BusinessRuleError) as exc:
        op_svc.pass_operation(OperationPassInput(
            sn=su.sn, work_station_id=ws_id,
            operator_id=sample_user.id, components=[], params=[]))
    assert "Issue #" in str(exc.value)

    # 3. supervisor acknowledge + resolve
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="主轴卡死", containment_action="已修",
                disposition="use_as_is")
    db_session.flush()
    assert svc.is_blocking(issue) is False  # resolved 不阻断

    # 4. 再 pass 通过（不再 raise IssueBlockError）
    try:
        op_svc.pass_operation(OperationPassInput(
            sn=su.sn, work_station_id=ws_id,
            operator_id=sample_user.id, components=[], params=[]))
    except BusinessRuleError as e:
        # 其他业务错误 OK，只要不是 Issue 阻断
        assert "Issue #" not in str(e)

    # 5. close（无 CAPA，直接通过）
    svc.close(issue.id, sample_user.id)
    db_session.refresh(issue)
    assert issue.status == "closed"


def test_e2e_capa_blocks_close(
        db_session, sample_user, full_station_setup):
    """有未验证 CAPA 时 close 失败。"""
    su = full_station_setup.serial_unit  # noqa: F841（占位保持 setup）
    t = IssueType(code="E2E_CAPA", name="b", severity="minor")
    db_session.add(t); db_session.flush()
    svc = IssueService(db_session)
    issue = svc.create_issue(
        issue_type_id=t.id, title="x",
        reported_by_id=sample_user.id)
    svc.acknowledge(issue.id, sample_user.id)
    svc.resolve(issue.id, sample_user.id,
                root_cause="r", containment_action="c",
                disposition="rework")
    svc.add_action(issue.id, type="corrective", title="a")  # status=open

    with pytest.raises(BusinessRuleError):
        svc.close(issue.id, sample_user.id)
```

- [ ] **Step 2: Run all issue tests**

Run: `uv run pytest tests/modules/issue/ -v`
Expected: 全部 PASS

- [ ] **Step 3: 回归全套**

Run: `uv run pytest -x`
Expected: 全部 PASS（无既有测试被破坏）

- [ ] **Step 4: 更新项目记忆**

在 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_lightmes.md` 末尾追加：

```markdown
## Issue / Andon 异常管理 — 2026-08-13

- 新模块 `src/lightmes/modules/issue/`：3 表（issue_types / issues / issue_actions）
- 状态机 OPEN → ACKNOWLEDGED → RESOLVED → CLOSED + supervisor 可 REOPEN
- CAPA 验证闸：close 必须所有 action verified
- SN 级阻断：`OperationPassService.pass_operation` 调 `IssueService.check_block_for_sn`
- Defect 联动：`DefectService.log_defect(create_issue=True)` 同事务建 issue
- 6 默认 type seed：material_shortage / quality / tool_failure / equipment_fault / safety / other
- 4 MCP 工具：list_issues / get_issue / create_issue / update_issue_status
- Station 阻断横幅红条 + ANDON 按钮启用（之前 disabled 占位）
- Issue/Defect/action 文本字段支持 `#N` 自动超链（issue_linkify Jinja filter）
- 不做：email/Slack 推送、多租户、软删除、JSON API v1 endpoints
```

- [ ] **Step 5: Commit**

```bash
git add tests/modules/issue/test_e2e.py
git commit -m "test(issue): end-to-end happy-path + CAPA-blocks-close scenario"
```

- [ ] **Step 6: PR / 收尾**

按项目惯例（README 或 CONTRIBUTING）开 PR 或 merge 到 master：

```bash
git push origin master  # 如直接 push
# 或
gh pr create --title "feat(issue): Issue/Andon module with CAPA + SN blocking + MCP tools"
```

---

## Self-Review 总结

**Spec coverage 检查**：
- ✓ §1 背景目标 → 全 plan 覆盖
- ✓ §2 数据模型（3 表 + seed） → Task 1
- ✓ §3 状态机 → Task 3 service
- ✓ §4 Station 集成 → Task 7
- ✓ §5 Defect 联动 → Task 8
- ✓ §6 #N 自动超链 → Task 4
- ✓ §7 UI 页面 → Task 5/6/9
- ✓ §8 权限矩阵 → Task 5/6 router `require_role`
- ✓ §9 MCP 工具 → Task 10
- ✓ §10 Migration → Task 1
- ✓ §11 文件改动清单 → 全部 task 累计覆盖
- ✓ §12 测试 → 每 task TDD + Task 11 E2E

**Placeholder 扫描**：无 TODO/TBD/"implement later"；每步都有完整代码。

**类型一致性**：
- `IssueService` 方法名贯穿：create_issue / acknowledge / resolve / close / reopen / add_action / start_action / complete_action / verify_action / is_blocking / check_block_for_sn / create_from_defect
- `IssueRepository` 方法名贯穿：get / list / get_blocking_for_sn / count_open / count_blocking / add
- 路径常量：`/issues` / `/issues/{id}` / `/issues/types` / `/issues/{id}/actions` / `/issues/actions/{aid}/*` / `/production/station/andon-form`
