# P2c 技能资格体系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 启用 operation 的技能资格要求（填 P2a 预留的 required_skill_id/required_level），过站时硬校验操作员技能等级，不足则拒绝过站；配套技能主数据 + 人员技能档案的管理界面。

**Architecture:** 先数据模型（skill + operator_skill 两张 MES 本地表 + operation.required_skill_id 补外键），再 SkillService（技能 CRUD、人员技能 upsert、get_operator_level 查询），再把 OperationPassService 的技能校验钩子从"默认放行"实现为"硬拦截"（SkillError 领域异常），最后 UI（技能页 + 人员技能页 + 路线编辑器加技能要求字段 + 首页导航）。沿用模块化单体全部约定。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`（`uv run`）。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0：`Mapped[]`/`mapped_column()`，继承 `Base`+`TimestampMixin`。所有 schema 变更走 Alembic；autogenerate 后**打开迁移确认只动预期表**，不得误删既有部分/唯一索引（uq_active_*/uq_operation_routing_seq/uq_operation_routing_code/uq_*_erp_ref/uq_bom_item_component 等）。
- **技能等级模型 = 单一数值等级（A）**：skill 有 max_level；operator_skill 一人一技能持一个 level；operation 要求一个 skill ≥ 一个 level。校验 = `operator_level >= required_level`。
- **skill / operator_skill 是 MES 本地主数据，无 source/erp_ref**（不同于 P2b product/bom）。
- **过站校验 = 硬拦截**：技能不足 → 抛 `SkillError`（继承 DomainError，status_code=422），拒绝过站、不写工序记录、不推进。位置：三层防跳站之后、写工序记录之前（operation_pass_service.py 现有 `# 5b. 技能校验钩子` 处）。
- **向后兼容**：operation.required_skill_id 为空 → 跳过校验直接过。
- **operator_id 空 + 有技能要求 → 拦截**（视为无技能，防匿名绕过）。
- 跨模块读写沿用 service 边界；领域异常全局 handler（已按 DomainError 基类统一处理，新异常继承即被捕获，无需改 handler）。事务边界 get_db；repository 只 flush。
- UI：HTMX 服务端渲染 + 薄荷绿卡片（复用 `.card`/`.data-table`/`.form-row`/`.badge` 等 app.css 样式）；写操作 require_login（页面用 `current_user_or_none`→401+HX-Redirect；API 用 `Depends(require_login)`）；`{{ }}` 自动转义。
- 提交前缀 `feat:`/`refactor:`/`chore:`/`test:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

P2c 结束时新增/修改：

```
src/lightmes/modules/masterdata/
├── models.py       # 改：加 Skill + OperatorSkill 模型；Operation.required_skill_id 补 ForeignKey("skill.id")
├── schemas.py      # 改：加 SkillCreate/SkillRead/OperatorSkillCreate/OperatorSkillRead；OperationCreate 加可选 required_skill_id/required_level
├── repository.py   # 改：加 SkillRepository / OperatorSkillRepository
├── skill_service.py# 新：SkillService（技能 CRUD、人员技能 upsert、get_operator_level）
├── service.py      # 改：create_routing 写 operation 时带上 required_skill_id/required_level
└── router.py       # 改：/masterdata/skills、/masterdata/operator-skills 管理页；路线编辑器 POST 收技能要求字段
src/lightmes/modules/auth/repository.py   # 改：UserRepository 加 list_all（人员技能页下拉用）
src/lightmes/modules/production/operation_pass_service.py  # 改：实现技能校验钩子（硬拦截）
src/lightmes/shared/errors.py             # 改：加 SkillError(DomainError, status_code=422)
src/lightmes/migrations/versions/         # 新：skill/operator_skill 表 + operation.required_skill_id FK
src/lightmes/templates/masterdata/        # 新：skills.html / operator_skills.html + 行 partials；改 routings.html 加技能字段
src/lightmes/templates/home.html          # 改：导航加技能管理/人员技能入口
tests/modules/masterdata/                 # 技能/人员技能 服务 + 页面测试
tests/modules/production/                 # 过站技能校验四态测试
```

> skill/operator_skill 放 masterdata 模块（都是主数据）。SkillService 独立文件（masterdata/skill_service.py），避免 service.py 继续膨胀。

---

### Task 1: 数据模型 skill + operator_skill + operation.required_skill_id 补外键 + 迁移

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`（加 Skill、OperatorSkill；Operation.required_skill_id 补 FK）
- Test: `tests/modules/masterdata/test_skill_models.py`
- Create: `src/lightmes/migrations/versions/<auto>_add_skill_tables.py`

**Interfaces:**
- Produces:
  - `Skill`（table `skill`）: `id` PK, `code: str`(unique, index), `name: str`, `max_level: int`, `description: str | None`(default None), + TimestampMixin
  - `OperatorSkill`（table `operator_skill`）: `id` PK, `user_id: int` FK users.id, `skill_id: int` FK skill.id, `level: int`, `__table_args__=(UniqueConstraint("user_id","skill_id",name="uq_operator_skill_user_skill"),)`, + TimestampMixin
  - `Operation.required_skill_id`：改为 `mapped_column(ForeignKey("skill.id"), default=None)`（仍 `int | None`）

- [ ] **Step 1: 加模型**

在 `masterdata/models.py`（确认顶部有 `from sqlalchemy import ForeignKey, UniqueConstraint`）。给 `Operation.required_skill_id` 补外键：
```python
    required_skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill.id"), default=None)
```
在文件末尾加两个模型：
```python
class Skill(Base, TimestampMixin):
    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    max_level: Mapped[int] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)


class OperatorSkill(Base, TimestampMixin):
    __tablename__ = "operator_skill"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_id", name="uq_operator_skill_user_skill"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skill.id"))
    level: Mapped[int] = mapped_column()
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "add skill and operator_skill tables"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移 create_table `skill`（含 unique index ix_skill_code）+ `operator_skill`（含 uq_operator_skill_user_skill + 两个 FK）+ 给 `operations.required_skill_id` 加 FK 约束（create_foreign_key → skill.id）。**打开迁移确认**：只建这两表 + 加那一个 FK，不误删任何既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component）。若 autogenerate 有多余 op，手工修正。

- [ ] **Step 3: 写测试**

`tests/modules/masterdata/test_skill_models.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError
from lightmes.modules.masterdata.models import Skill, OperatorSkill
from lightmes.modules.auth.models import User


def _user(db_session, uname):
    u = User(username=uname, password_hash="x", display_name=uname)
    db_session.add(u); db_session.flush(); return u


def test_create_skill(db_session):
    s = Skill(code="ASSY", name="装配", max_level=3)
    db_session.add(s); db_session.flush()
    assert s.id is not None and s.description is None


def test_operator_skill_unique_per_user_skill(db_session):
    u = _user(db_session, "op1")
    s = Skill(code="SK1", name="技能1", max_level=3)
    db_session.add(s); db_session.flush()
    db_session.add(OperatorSkill(user_id=u.id, skill_id=s.id, level=2))
    db_session.flush()
    db_session.add(OperatorSkill(user_id=u.id, skill_id=s.id, level=3))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_same_skill_different_users_ok(db_session):
    u1 = _user(db_session, "opA"); u2 = _user(db_session, "opB")
    s = Skill(code="SK2", name="技能2", max_level=3)
    db_session.add(s); db_session.flush()
    db_session.add(OperatorSkill(user_id=u1.id, skill_id=s.id, level=1))
    db_session.add(OperatorSkill(user_id=u2.id, skill_id=s.id, level=2))
    db_session.flush()  # 无异常即通过
```

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_skill_models.py -v` → PASS（3）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata/models.py src/lightmes/migrations tests/modules/masterdata/test_skill_models.py
git commit -m "feat: add skill and operator_skill models with operation FK"
```

---

### Task 2: SkillService（技能 CRUD、人员技能 upsert、get_operator_level）

**Files:**
- Modify: `src/lightmes/modules/masterdata/repository.py`（加 SkillRepository、OperatorSkillRepository）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（加 Skill/OperatorSkill schemas）
- Create: `src/lightmes/modules/masterdata/skill_service.py`
- Modify: `src/lightmes/modules/auth/repository.py`（UserRepository 加 list_all）
- Test: `tests/modules/masterdata/test_skill_service.py`

**Interfaces:**
- Consumes: Skill/OperatorSkill 模型（Task 1）；User 模型。
- Produces:
  - `SkillRepository(db)`: `add(skill) -> Skill`; `get(id) -> Skill | None`; `get_by_code(code) -> Skill | None`; `list_all() -> list[Skill]`
  - `OperatorSkillRepository(db)`: `add(os) -> OperatorSkill`; `get_by_user_skill(user_id, skill_id) -> OperatorSkill | None`; `list_all() -> list[OperatorSkill]`
  - `schemas.SkillCreate`(code, name, max_level, description=None), `SkillRead`(from_attributes: id, code, name, max_level, description)
  - `schemas.OperatorSkillCreate`(user_id, skill_id, level), `OperatorSkillRead`(from_attributes: id, user_id, skill_id, level)
  - `SkillService(db)`:
    - `create_skill(SkillCreate) -> Skill`（dup code → ValueError）
    - `list_skills() -> list[Skill]`
    - `set_operator_skill(user_id: int, skill_id: int, level: int) -> OperatorSkill`（upsert；user/skill 不存在 → ValueError；level<1 或 >skill.max_level → ValueError；存在则更新 level 否则新建）
    - `list_operator_skills() -> list[OperatorSkill]`
    - `get_operator_level(user_id: int, skill_id: int) -> int | None`（无记录返回 None）
  - `UserRepository.list_all() -> list[User]`

- [ ] **Step 1: repository + UserRepository.list_all**

在 `masterdata/repository.py`（确认 `select` 已 import；import Skill/OperatorSkill）末尾加：
```python
class SkillRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, skill: Skill) -> Skill:
        self.db.add(skill); self.db.flush(); return skill

    def get(self, skill_id: int) -> Skill | None:
        return self.db.get(Skill, skill_id)

    def get_by_code(self, code: str) -> Skill | None:
        return self.db.execute(
            select(Skill).where(Skill.code == code)).scalar_one_or_none()

    def list_all(self) -> list[Skill]:
        return list(self.db.execute(select(Skill)).scalars().all())


class OperatorSkillRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, os: OperatorSkill) -> OperatorSkill:
        self.db.add(os); self.db.flush(); return os

    def get_by_user_skill(self, user_id: int, skill_id: int) -> OperatorSkill | None:
        return self.db.execute(
            select(OperatorSkill).where(
                OperatorSkill.user_id == user_id,
                OperatorSkill.skill_id == skill_id)).scalar_one_or_none()

    def list_all(self) -> list[OperatorSkill]:
        return list(self.db.execute(select(OperatorSkill)).scalars().all())
```
在 `auth/repository.py` `UserRepository` 加：
```python
    def list_all(self) -> list[User]:
        return list(self.db.execute(select(User)).scalars().all())
```

- [ ] **Step 2: schemas**

在 `masterdata/schemas.py` 加：
```python
class SkillCreate(BaseModel):
    code: str
    name: str
    max_level: int
    description: str | None = None


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    max_level: int
    description: str | None


class OperatorSkillCreate(BaseModel):
    user_id: int
    skill_id: int
    level: int


class OperatorSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    skill_id: int
    level: int
```

- [ ] **Step 3: 写失败测试**

`tests/modules/masterdata/test_skill_service.py`:
```python
import pytest
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import SkillCreate
from lightmes.modules.auth.models import User


def _user(db_session, uname="op"):
    u = User(username=uname, password_hash="x", display_name=uname)
    db_session.add(u); db_session.flush(); return u


def test_create_skill_and_list(db_session):
    svc = SkillService(db_session)
    s = svc.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    assert s.id is not None
    assert [x.code for x in svc.list_skills()] == ["ASSY"]


def test_create_skill_dup_code_raises(db_session):
    svc = SkillService(db_session)
    svc.create_skill(SkillCreate(code="DUP", name="a", max_level=3))
    with pytest.raises(ValueError):
        svc.create_skill(SkillCreate(code="DUP", name="b", max_level=3))


def test_set_operator_skill_creates_then_updates(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK", name="技能", max_level=3))
    os1 = svc.set_operator_skill(u.id, s.id, 2)
    assert os1.level == 2
    os2 = svc.set_operator_skill(u.id, s.id, 3)  # upsert → 更新
    assert os2.id == os1.id and os2.level == 3
    assert len(svc.list_operator_skills()) == 1


def test_set_operator_skill_level_out_of_range_raises(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK2", name="技能", max_level=3))
    with pytest.raises(ValueError):
        svc.set_operator_skill(u.id, s.id, 4)  # >max_level
    with pytest.raises(ValueError):
        svc.set_operator_skill(u.id, s.id, 0)  # <1


def test_set_operator_skill_unknown_user_or_skill_raises(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK3", name="技能", max_level=3))
    with pytest.raises(ValueError):
        svc.set_operator_skill(99999, s.id, 1)
    with pytest.raises(ValueError):
        svc.set_operator_skill(u.id, 99999, 1)


def test_get_operator_level(db_session):
    svc = SkillService(db_session)
    u = _user(db_session)
    s = svc.create_skill(SkillCreate(code="SK4", name="技能", max_level=3))
    assert svc.get_operator_level(u.id, s.id) is None
    svc.set_operator_skill(u.id, s.id, 2)
    assert svc.get_operator_level(u.id, s.id) == 2
```

- [ ] **Step 4: 运行确认失败，写 SkillService**

`src/lightmes/modules/masterdata/skill_service.py`:
```python
from sqlalchemy.orm import Session

from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.models import Skill, OperatorSkill
from lightmes.modules.masterdata.repository import (
    SkillRepository, OperatorSkillRepository,
)
from lightmes.modules.masterdata.schemas import SkillCreate


class SkillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.skills = SkillRepository(db)
        self.operator_skills = OperatorSkillRepository(db)
        self.users = UserRepository(db)

    def create_skill(self, data: SkillCreate) -> Skill:
        if self.skills.get_by_code(data.code) is not None:
            raise ValueError(f"技能编码已存在: {data.code}")
        return self.skills.add(Skill(
            code=data.code, name=data.name,
            max_level=data.max_level, description=data.description))

    def list_skills(self) -> list[Skill]:
        return self.skills.list_all()

    def set_operator_skill(self, user_id: int, skill_id: int, level: int) -> OperatorSkill:
        if self.users.get(user_id) is None:
            raise ValueError(f"用户不存在: {user_id}")
        skill = self.skills.get(skill_id)
        if skill is None:
            raise ValueError(f"技能不存在: {skill_id}")
        if level < 1 or level > skill.max_level:
            raise ValueError(f"等级越界: {level}（1..{skill.max_level}）")
        existing = self.operator_skills.get_by_user_skill(user_id, skill_id)
        if existing is not None:
            existing.level = level
            self.db.flush()
            return existing
        return self.operator_skills.add(OperatorSkill(
            user_id=user_id, skill_id=skill_id, level=level))

    def list_operator_skills(self) -> list[OperatorSkill]:
        return self.operator_skills.list_all()

    def get_operator_level(self, user_id: int, skill_id: int) -> int | None:
        os = self.operator_skills.get_by_user_skill(user_id, skill_id)
        return os.level if os is not None else None
```
注意：`UserRepository` 需要一个 `get(user_id)` 方法。若 UserRepository 无 `get`，在 auth/repository.py 加：
```python
    def get(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)
```
（Step 1 已加 list_all；此处一并确认 get 存在，缺则补。）

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run → PASS（6）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata src/lightmes/modules/auth/repository.py tests/modules/masterdata/test_skill_service.py
git commit -m "feat: add SkillService (skill CRUD, operator skill upsert, level query)"
```

---

### Task 3: SkillError + 过站技能校验（硬拦截）

**Files:**
- Modify: `src/lightmes/shared/errors.py`（加 SkillError）
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（实现 `# 5b. 技能校验钩子`）
- Test: `tests/modules/production/test_operation_pass_skill.py`

**Interfaces:**
- Consumes: `SkillService.get_operator_level`（Task 2）；expected operation 的 `required_skill_id`/`required_level`；`data.operator_id`。
- Produces:
  - `errors.SkillError(DomainError)` with `status_code = 422`
  - OperationPassService 校验：三层防跳站之后、写 OperationRecord 之前——若 `expected.required_skill_id is not None` 则查操作员该技能等级，None 或 < required_level → raise SkillError；required_skill_id 为空 → 跳过。

- [ ] **Step 1: 加 SkillError**

在 `src/lightmes/shared/errors.py` 末尾加：
```python
class SkillError(DomainError):
    status_code = 422
```

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_operation_pass_skill.py`（校验四态）。参考现有 `tests/modules/production/test_operation_pass.py` 的 fixture 构建方式（line + work_station + product + routing with operations + released work_order + user），额外用 SkillService 建技能 + 给操作员设等级、并在需要处给 operation 设 required_skill_id/required_level：
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import SkillError


def _setup(db_session, required_skill=False, op_level=None, operator_level=None):
    md = MasterDataService(db_session)
    sk = SkillService(db_session)
    user = User(username="skop", password_hash="x", display_name="技工")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    skill = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id)]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    if required_skill:
        op = [o for o in md.routings.operations_of(routing.id)][0]
        op.required_skill_id = skill.id
        op.required_level = op_level
        db_session.flush()
    if operator_level is not None:
        sk.set_operator_skill(user.id, skill.id, operator_level)
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, ws, user


def test_pass_no_skill_requirement_ok(db_session):
    db, ws, user = _setup(db_session, required_skill=False)
    r = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="WO", operator_id=user.id))
    assert r.sn is not None  # 无技能要求 → 放行


def test_pass_sufficient_skill_ok(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=2, operator_level=3)
    r = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="WO", operator_id=user.id))
    assert r.sn is not None  # 3 >= 2 → 放行


def test_pass_insufficient_skill_blocked(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=3, operator_level=1)
    with pytest.raises(SkillError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="WO", operator_id=user.id))


def test_pass_no_operator_skill_record_blocked(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=2, operator_level=None)
    with pytest.raises(SkillError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="WO", operator_id=user.id))


def test_pass_no_operator_id_with_requirement_blocked(db_session):
    db, ws, user = _setup(db_session, required_skill=True, op_level=2, operator_level=3)
    with pytest.raises(SkillError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="WO", operator_id=None))
```
（若 `md.routings.operations_of` 不存在，用 `RoutingRepository` 已有的取工序方法——grep 确认名字；P2a 用的是 operations_of，若不同则改。）

- [ ] **Step 3: 运行确认失败，实现钩子**

在 `operation_pass_service.py` 顶部 import 加 `from lightmes.shared.errors import ..., SkillError`（把 SkillError 加入现有 errors import），并 `from lightmes.modules.masterdata.skill_service import SkillService`。把第 90-91 行的钩子注释替换为：
```python
        # 5b. 技能校验（硬拦截）：工序有技能要求时，操作员该技能等级须 >= 要求
        if expected.required_skill_id is not None:
            level = (SkillService(self.db).get_operator_level(
                data.operator_id, expected.required_skill_id)
                if data.operator_id else None)
            if level is None or level < (expected.required_level or 0):
                raise SkillError(
                    f"操作员技能不足：工序 {expected.seq} {expected.name} "
                    f"需要技能等级 L{expected.required_level}+，当前 "
                    f"{level if level is not None else '无'}")
```

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_skill.py -v` → PASS（5：无要求过、足够过、不足拦、无档案拦、无operator_id拦）。
全量回归 → 全绿（现有 operation_pass 测试的工序无技能要求，不受影响）。
```bash
git add src/lightmes/shared/errors.py src/lightmes/modules/production/operation_pass_service.py tests/modules/production/test_operation_pass_skill.py
git commit -m "feat: enforce operator skill qualification at operation pass (hard block)"
```

---

### Task 4: 技能定义页 + 人员技能档案页 + 首页导航

**Files:**
- Modify: `src/lightmes/modules/masterdata/router.py`（skills / operator-skills 页面）
- Create: `src/lightmes/templates/masterdata/skills.html` + `partials/skill_row.html`; `operator_skills.html` + `partials/operator_skill_row.html`
- Modify: `src/lightmes/templates/home.html`（导航加两入口）
- Test: `tests/modules/masterdata/test_skill_pages.py`

**Interfaces:**
- Consumes: `SkillService`（Task 2）；`UserRepository.list_all`；`current_user_or_none`（写守卫）。
- Produces:
  - `GET /masterdata/skills`（列表）+ `POST /masterdata/skills`（新增：code/name/max_level/description；dup code → error_row colspan=4）
  - `GET /masterdata/operator-skills`（列表 + 下拉数据 users/skills）+ `POST /masterdata/operator-skills`（user_id/skill_id/level → set_operator_skill upsert；ValueError → error_row colspan=3）
  - home.html 加 `/masterdata/skills`、`/masterdata/operator-skills` nav-cards

- [ ] **Step 1: 模板**

`src/lightmes/templates/masterdata/skills.html`（复用 P2b lines.html 结构）:
```html
{% extends "base.html" %}
{% block title %}技能管理{% endblock %}
{% block content %}
<h1 class="page-title">技能管理</h1>
<div class="card">
  <div class="card__title">新增技能</div>
  <form class="form-row" hx-post="/masterdata/skills" hx-target="#rows" hx-swap="beforeend"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="field"><label>编码</label><input name="code" required></div>
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field"><label>最高等级</label><input name="max_level" type="number" required></div>
    <div class="field" style="flex:1"><label>描述</label><input name="description"></div>
    <button type="submit">新增</button>
  </form>
</div>
<div class="card">
  <div class="card__title">技能列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>最高等级</th></tr></thead>
    <tbody id="rows">
      {% for s in skills %}{% include "masterdata/partials/skill_row.html" %}{% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```
`partials/skill_row.html`:
```html
<tr><td>{{ s.id }}</td><td>{{ s.code }}</td><td>{{ s.name }}</td><td>L{{ s.max_level }}</td></tr>
```
`operator_skills.html`:
```html
{% extends "base.html" %}
{% block title %}人员技能{% endblock %}
{% block content %}
<h1 class="page-title">人员技能档案</h1>
<div class="card">
  <div class="card__title">设置人员技能（同人同技能重复=更新等级）</div>
  <form class="form-row" hx-post="/masterdata/operator-skills" hx-target="#rows" hx-swap="beforeend"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="field"><label>人员</label>
      <select name="user_id">{% for u in users %}<option value="{{ u.id }}">{{ u.display_name }}</option>{% endfor %}</select>
    </div>
    <div class="field"><label>技能</label>
      <select name="skill_id">{% for s in skills %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select>
    </div>
    <div class="field"><label>等级</label><input name="level" type="number" required></div>
    <button type="submit">保存</button>
  </form>
</div>
<div class="card">
  <div class="card__title">档案列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>人员</th><th>技能</th><th>等级</th></tr></thead>
    <tbody id="rows">
      {% for os in operator_skills %}{% include "masterdata/partials/operator_skill_row.html" %}{% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```
`partials/operator_skill_row.html`（行需展示人名/技能名——POST 后渲染单行时也要能取到，故传 `user`/`skill` 对象或名字；见 Step 3 处理器）:
```html
<tr><td>{{ os.id }}</td><td>{{ os.user_id }}</td><td>{{ os.skill_id }}</td><td>L{{ os.level }}</td></tr>
```
（MVP：列表行显示 id 即可；如需人名/技能名，处理器可预取 dict 映射传模板。本期用 id 保持最简，YAGNI。）

- [ ] **Step 2: 写失败测试**

`tests/modules/masterdata/test_skill_pages.py`（TestClient + 登录 fixture，参考 test_masterdata_pages.py）:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import SkillCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="sk", password="pw12345", display_name="Sk"))
    db_session.flush()
    client.post("/login", data={"username": "sk", "password": "pw12345"})


def test_skills_page_and_create(client, db_session):
    _login(client, db_session)
    assert client.get("/masterdata/skills").status_code == 200
    resp = client.post("/masterdata/skills", data={"code": "ASSY", "name": "装配", "max_level": "3", "description": ""})
    assert resp.status_code == 200 and "ASSY" in resp.text


def test_skills_create_requires_login(client, db_session):
    resp = client.post("/masterdata/skills", data={"code": "X", "name": "x", "max_level": "3", "description": ""})
    assert resp.status_code == 401


def test_operator_skills_page_and_upsert(client, db_session):
    _login(client, db_session)
    sk = SkillService(db_session)
    s = sk.create_skill(SkillCreate(code="SK", name="技能", max_level=3))
    db_session.flush()
    # 当前登录用户 sk 已存在；取其 id 通过页面下拉不便，直接用 service 侧已知：用 users list
    assert client.get("/masterdata/operator-skills").status_code == 200
    # 找到 sk 用户 id
    from lightmes.modules.auth.repository import UserRepository
    uid = UserRepository(db_session).get_by_username("sk").id
    resp = client.post("/masterdata/operator-skills", data={"user_id": str(uid), "skill_id": str(s.id), "level": "2"})
    assert resp.status_code == 200
    # upsert：再设一次更高等级，仍 200，不新增第二条
    resp2 = client.post("/masterdata/operator-skills", data={"user_id": str(uid), "skill_id": str(s.id), "level": "3"})
    assert resp2.status_code == 200
    assert len(sk.list_operator_skills()) == 1


def test_operator_skills_level_out_of_range_error(client, db_session):
    _login(client, db_session)
    sk = SkillService(db_session)
    s = sk.create_skill(SkillCreate(code="SK2", name="技能", max_level=3))
    db_session.flush()
    from lightmes.modules.auth.repository import UserRepository
    uid = UserRepository(db_session).get_by_username("sk").id
    resp = client.post("/masterdata/operator-skills", data={"user_id": str(uid), "skill_id": str(s.id), "level": "9"})
    assert resp.status_code == 200  # error_row 片段
    assert "越界" in resp.text
```

- [ ] **Step 3: 运行确认失败，写路由 + 导航**

在 `masterdata/router.py`（import SkillService、SkillCreate；UserRepository）加四个处理器，镜像现有 lines 页模式（GET 无 auth；POST current_user_or_none→401+HX-Redirect；成功 row 片段，ValueError → `masterdata/partials/error_row.html` {error, colspan}）：
- `GET /masterdata/skills` → 传 `skills=SkillService(db).list_skills()`
- `POST /masterdata/skills`（Form code/name/max_level:int/description）→ create_skill；成功 skill_row，dup → error_row colspan=4
- `GET /masterdata/operator-skills` → 传 `operator_skills=list_operator_skills()`, `users=UserRepository(db).list_all()`, `skills=list_skills()`
- `POST /masterdata/operator-skills`（Form user_id:int/skill_id:int/level:int）→ set_operator_skill；成功 operator_skill_row，ValueError（越界/不存在）→ error_row colspan=3
在 `home.html` 主数据卡片区加：
```html
    <a class="nav-card" href="/masterdata/skills"><span class="nav-card__icon">🎓</span>
      <div class="nav-card__name">技能管理</div><div class="nav-card__desc">技能与等级定义</div></a>
    <a class="nav-card" href="/masterdata/operator-skills"><span class="nav-card__icon">👷</span>
      <div class="nav-card__name">人员技能</div><div class="nav-card__desc">操作员资格档案</div></a>
```

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run → PASS。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata/router.py src/lightmes/templates/masterdata src/lightmes/templates/home.html tests/modules/masterdata/test_skill_pages.py
git commit -m "feat: add skill and operator-skill management pages with nav"
```

---

### Task 5: 路线编辑器加技能要求字段

**Files:**
- Modify: `src/lightmes/modules/masterdata/schemas.py`（OperationCreate 加可选 required_skill_id/required_level）
- Modify: `src/lightmes/modules/masterdata/service.py`（create_routing 写 operation 时带上技能要求）
- Modify: `src/lightmes/modules/masterdata/router.py`（routings_create_page 收技能字段；routings_page 传 skills 给下拉）
- Modify: `src/lightmes/templates/masterdata/routings.html`（每工序行加技能下拉 + 等级；列表展示技能要求）
- Test: `tests/modules/masterdata/test_routing_skill.py`

**Interfaces:**
- Consumes: OperationCreate；create_routing（P2a）；Skill 列表。
- Produces:
  - `OperationCreate` 加 `required_skill_id: int | None = None`, `required_level: int | None = None`
  - `create_routing` 建 Operation 时写 `required_skill_id=op.required_skill_id, required_level=op.required_level`
  - `POST /masterdata/routings` 多收 `op_skill: list[str]`（技能id，可空="无"）+ `op_level: list[str]`（等级，可空）；zip 进 OperationCreate（空技能→None/None）
  - `GET /masterdata/routings` 传 `skills` 给每行技能下拉

- [ ] **Step 1: schema + service**

在 `masterdata/schemas.py` `OperationCreate` 加两字段：
```python
class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    is_mandatory: bool = True
    required_skill_id: int | None = None
    required_level: int | None = None
```
在 `masterdata/service.py` `create_routing` 建 Operation 处加两字段：
```python
            self.db.add(Operation(
                routing_id=routing.id,
                seq=op.seq,
                code=op.code,
                name=op.name,
                default_work_station_id=op.default_work_station_id,
                is_mandatory=op.is_mandatory,
                required_skill_id=op.required_skill_id,
                required_level=op.required_level,
            ))
```

- [ ] **Step 2: 写失败测试**

`tests/modules/masterdata/test_routing_skill.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, SkillCreate,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="rs", password="pw12345", display_name="Rs"))
    db_session.flush()
    client.post("/login", data={"username": "rs", "password": "pw12345"})


def test_routing_create_with_skill_requirement(client, db_session):
    md = MasterDataService(db_session); sk = SkillService(db_session)
    p = md.create_product(ProductCreate(code="RP", name="件", type="finished"))
    line = md.create_line(LineCreate(code="RL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RW", name="站", line_id=line.id, seq=1))
    s = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data=[
        ("code", "RT1"), ("name", "路线"), ("product_id", str(p.id)),
        ("op_seq", "1"), ("op_code", "OP1"), ("op_name", "上料"), ("op_ws", str(w.id)),
        ("op_skill", str(s.id)), ("op_level", "2"),
    ])
    assert resp.status_code == 200
    routing = md.routings.get_by_code("RT1")
    op = md.routings.operations_of(routing.id)[0]
    assert op.required_skill_id == s.id and op.required_level == 2


def test_routing_create_without_skill_leaves_null(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RP2", name="件", type="finished"))
    line = md.create_line(LineCreate(code="RL2", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RW2", name="站", line_id=line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    resp = client.post("/masterdata/routings", data=[
        ("code", "RT2"), ("name", "路线"), ("product_id", str(p.id)),
        ("op_seq", "1"), ("op_code", "OP1"), ("op_name", "上料"), ("op_ws", str(w.id)),
        ("op_skill", ""), ("op_level", ""),  # 无技能要求
    ])
    assert resp.status_code == 200
    routing = md.routings.get_by_code("RT2")
    op = md.routings.operations_of(routing.id)[0]
    assert op.required_skill_id is None and op.required_level is None
```
（确认 `RoutingRepository.operations_of(routing_id)` 存在——P2a 建；若名字不同，grep 后改测试。）

- [ ] **Step 3: 运行确认失败，写路由 + 模板**

在 `masterdata/router.py`：
- `routings_page` 加 `skills = svc.list_skills()`（或 `SkillService(db).list_skills()`）传模板。
- `routings_create_page` 签名加 `op_skill: list[str] = Form(default=[])`, `op_level: list[str] = Form(default=[])`；在构造 operations 的循环里（try 内，与现有 int 转换同处）把技能字段并入 zip：
```python
        for seq, c, n, ws, sk_id, lvl in zip(op_seq, op_code, op_name, op_ws, op_skill, op_level):
            if not c.strip() or not ws.strip():
                continue
            operations.append(OperationCreate(
                seq=int(seq), code=c.strip(), name=n.strip(),
                default_work_station_id=int(ws),
                required_skill_id=int(sk_id) if sk_id.strip() else None,
                required_level=int(lvl) if lvl.strip() else None))
```
  注意 zip 长度对齐：op_skill/op_level 与其他四列同为每行一项（模板每行都渲染这两个输入）。若某行技能空则 None。int 转换在 try 内（沿用 Task/P2b 的 int 守卫，非法→ValueError→routing_error 片段）。
- `routings.html` 每工序行加两个字段（在作业站下拉后）：
```html
      <div class="field"><label>技能</label>
        <select name="op_skill"><option value="">无</option>{% for s in skills %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select>
      </div>
      <div class="field"><label>要求等级</label><input name="op_level" type="number"></div>
```
  路线列表工序展示可选带技能要求（如有 required_skill_id 显示 "L{level}+"）——MVP 可仅在列表保持现状，YAGNI；若展示则在 routings.html 列表区读取。

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run → PASS（2）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata src/lightmes/templates/masterdata/routings.html tests/modules/masterdata/test_routing_skill.py
git commit -m "feat: routing editor sets operation skill requirement"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2c spec §3/§4/§5/§6）：
- skill + operator_skill 两表 + operation.required_skill_id 补 FK + 迁移 → Task 1 ✅
- SkillService（create_skill/list_skills、set_operator_skill upsert、list_operator_skills、get_operator_level）→ Task 2 ✅
- SkillError + 过站硬拦截钩子（向后兼容、operator_id 空拦截）→ Task 3 ✅
- 技能页 + 人员技能页 + 首页导航 → Task 4 ✅
- 路线编辑器加技能要求字段 → Task 5 ✅
- 校验四态测试 → Task 3（无要求过/足够过/不足拦/无档案拦/无operator_id拦=5态，覆盖 spec 四态+额外无档案态）✅

**占位符扫描**：Task 4 Step 3 与 Task 5 Step 3 的页面路由处理器给了"镜像现有 lines/routings 页模式"的指引而非逐行完整代码——因为它们把已存在的 P2b 页 CRUD 模式（current_user_or_none + Form + 片段 + rollback）机械套用到技能实体，模板与关键循环代码已完整给出。实现时对照 `masterdata/router.py` 现有 lines/routings 处理器套用。其余步骤含完整代码。

**类型一致性**：`Skill`/`OperatorSkill`、`SkillCreate/SkillRead/OperatorSkillCreate/OperatorSkillRead`、`SkillRepository/OperatorSkillRepository`（含 get/get_by_code/get_by_user_skill/list_all）、`SkillService`（create_skill/list_skills/set_operator_skill/list_operator_skills/get_operator_level）、`UserRepository.list_all/get`、`SkillError`、`OperationCreate.required_skill_id/required_level`、`RoutingRepository.operations_of` —— 定义处与引用处一致 ✅。

**迁移**：Task 1 建两表 + operation.required_skill_id 加 FK；打开迁移核对不误删既有索引。

**依赖校验**：Task 2 引用 `UserRepository.get`——Step 4 明确"缺则补"。Task 3/5 引用 `RoutingRepository.operations_of`——P2a 已建，测试若名字不符则 grep 校正（已在步骤注明）。
