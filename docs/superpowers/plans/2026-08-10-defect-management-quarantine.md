# 缺陷管理 + 不良品隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立缺陷记录闭环——发现缺陷 → SN 自动隔离（`quarantined` 状态）→ 处理决策（返工/报废/让步）→ 解除隔离。新 2 张表（DefectType + DefectRecord）+ SN 新状态值 + 复用既有 rework/scrap。

**Architecture:** 新 `DefectService`（log_defect 隔离 SN + handle_rework/scrap/concession 三路决策）；`SerialUnit.status` 加 `"quarantined"` 值（无字段改动）；`pass_operation` / `skip_operation` 步骤 1 拒绝集合加 quarantined；`ReworkService.scrap` 允许集合扩为含 quarantined + finished；缺陷类型主数据 CRUD 页 + 缺陷登记页 + 缺陷列表/详情页（详情页内嵌返工表单 target_seq 下拉 + HTMX 联动站位，复用 P2h 模式）。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试/迁移命令用 `127.0.0.1`（非 localhost）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- **新 2 张表**：`defect_types`（主数据，code unique）+ `defect_records`（实例，含 type code/name/severity 快照字段）。一条 Alembic 迁移 `create_table` 两表 + FK + 索引，不删/改既有表。
- **`SerialUnit.status` 新值 `"quarantined"`**：无字段改动（既有 string 列），无迁移。
- **让步回原状态**：**一律回 `in_process`**（不记 pre_quarantine_status）。
- **发现即隔离**：`log_defect` 后 `su.status = "quarantined"`（除非已 scrapped/quarantined，拒绝）。
- **状态机适配**：`pass_operation` / `skip_operation` 步骤 1 拒绝集合 `("finished", "scrapped")` 扩为 `("finished", "scrapped", "quarantined")`；`ReworkService.scrap` 允许集合 `("in_process", "reworking")` 扩为 `("in_process", "reworking", "quarantined", "finished")`；`ReworkService.rework` 不改（仅拒 scrapped）。
- **让步授权**：`concession` 需 supervisor/admin 角色（复用 `require_role`）。
- **返工 target_seq**：缺陷详情页内嵌表单，操作员下拉选 target_seq + HTMX 联动站位下拉（复用 P2h `/trace/rework/allowed-stations` 模式）。
- SQLAlchemy 2.0 风格；Pydantic v2；commit 前缀 `feat:`/`refactor:`/`test:`/`fix:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。
- **测试隔离**：跑测试前清库：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"`

---

## File Structure

```
src/lightmes/modules/production/
├── models.py                    # 改：新增 DefectType + DefectRecord
├── defect_service.py            # 新：DefectService（log/handle_rework/handle_scrap/handle_concession）
├── events.py                    # 改：新增 DefectLogged + DefectHandled 事件
├── operation_pass_service.py    # 改：pass/skip 步骤 1 拒绝 quarantined
src/lightmes/modules/trace/
├── rework_service.py            # 改：scrap 允许 quarantined + finished
src/lightmes/modules/quality/
├── router.py                    # 改：新增 11 个路由（defect-types CRUD + defects log/list/detail/handle）
├── defect_router.py             # 新（可选拆分，若 router.py 过大）：defect 相关路由分离
src/lightmes/migrations/versions/
└── xxx_create_defect_tables.py  # 新迁移
src/lightmes/templates/quality/
├── defect_types.html            # 新
├── defect_log.html              # 新
├── defect_list.html             # 新
├── defect_detail.html           # 新
└── partials/
    ├── defect_type_row.html     # 新
    ├── defect_log_success.html  # 新
    └── rework_stations.html      # 新（HTMX 联动站位片段）
src/lightmes/templates/home.html # 改：质量管理卡片加 4 个入口
src/lightmes/static/css/app.css  # 改：缺陷状态 badge 颜色 + quarantine 警告样式
tests/modules/production/
├── test_defect_service.py       # 新
├── test_defect_state_machine.py # 新（pass/skip/rework quarantined 适配）
└── test_defect_e2e.py           # 新
tests/modules/quality/
└── test_defect_routes.py        # 新
```

---

### Task 1: 数据模型 + 迁移

**Files:**
- Modify: `src/lightmes/modules/production/models.py`（新增 DefectType + DefectRecord）
- Create: `src/lightmes/migrations/versions/<auto>_create_defect_tables.py`
- Test: `tests/modules/production/test_defect_models.py`（新）

**Interfaces:**
- Produces:
  - `DefectType` model (id/code unique/name/category/severity/description/is_active + TimestampMixin)
  - `DefectRecord` model (id/defect_type_id FK/defect_type_code/defect_type_name/severity 快照/serial_unit_id FK/work_order_id FK/operation_id FK nullable/work_station_id FK nullable/position/discovered_by/discovered_at/handling_status/handled_by nullable/handled_at nullable/handling_remark nullable/remark nullable + TimestampMixin)

- [ ] **Step 1: 加 DefectType + DefectRecord 模型**

在 `src/lightmes/modules/production/models.py` 末尾（既有 TestDataValue 类之后）加：
```python
class DefectType(Base, TimestampMixin):
    """缺陷类型主数据"""
    __tablename__ = "defect_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    category: Mapped[str | None] = mapped_column(default=None)  # 外观/尺寸/功能/其他
    severity: Mapped[str] = mapped_column(default="major")  # critical/major/minor
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class DefectRecord(Base, TimestampMixin):
    """缺陷记录（实例）"""
    __tablename__ = "defect_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    defect_type_id: Mapped[int] = mapped_column(
        ForeignKey("defect_types.id"), index=True)
    defect_type_code: Mapped[str] = mapped_column()  # 快照
    defect_type_name: Mapped[str] = mapped_column()  # 快照
    severity: Mapped[str] = mapped_column()  # 快照（登记时刻）
    serial_unit_id: Mapped[int] = mapped_column(
        ForeignKey("serial_units.id"), index=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("operations.id"), default=None)
    work_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), default=None)
    position: Mapped[str | None] = mapped_column(default=None)
    discovered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    handling_status: Mapped[str] = mapped_column(default="pending")  # pending/rework/scrap/concession
    handled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None)
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    handling_remark: Mapped[str | None] = mapped_column(default=None)
    remark: Mapped[str | None] = mapped_column(default=None)
```

确认顶部 import 含 `datetime`、`ForeignKey`、`func`（现状已有）。

- [ ] **Step 2: 生成迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create_defect_tables"
```

- [ ] **Step 3: 校验迁移**

打开生成的迁移文件，确认 `upgrade()` 仅含 `op.create_table("defect_types", ...)` + `op.create_table("defect_records", ...)` + 对应 `op.create_index` + `downgrade()` 含 `op.drop_table`。若 autogenerate 误删既有索引/约束，**手动删掉那些 op 行**。

- [ ] **Step 4: 跑迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: `Running upgrade <prev> -> <new>, create_defect_tables`

- [ ] **Step 5: 写模型测试**

创建 `tests/modules/production/test_defect_models.py`：
```python
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType, DefectRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User


def _setup_sn(db):
    md = MasterDataService(db)
    user = User(username="dmop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DML", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="DMW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="DMP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1",
                           default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="DMRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DMSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DMWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="DMWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    return su, user, wo


def test_defect_type_persist(db_session):
    db = db_session
    dt = DefectType(code="SCRATCH", name="划伤", category="外观", severity="major")
    db.add(dt); db.flush()
    db.refresh(dt)
    assert dt.id is not None
    assert dt.is_active is True
    assert dt.severity == "major"


def test_defect_record_persist_with_snapshot(db_session):
    db = db_session
    su, user, wo = _setup_sn(db)
    dt = DefectType(code="DENT", name="凹陷", category="外观", severity="critical")
    db.add(dt); db.flush()
    rec = DefectRecord(
        defect_type_id=dt.id, defect_type_code=dt.code, defect_type_name=dt.name,
        severity=dt.severity, serial_unit_id=su.id, work_order_id=wo.id,
        discovered_by=user.id, handling_status="pending")
    db.add(rec); db.flush()
    db.refresh(rec)
    assert rec.id is not None
    assert rec.defect_type_code == "DENT"
    assert rec.severity == "critical"
    assert rec.handling_status == "pending"
    assert rec.discovered_at is not None
```

- [ ] **Step 6: 跑测试**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_models.py -v
```
Expected: 2 PASS

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/modules/production/models.py src/lightmes/migrations/versions/*_create_defect_tables.py tests/modules/production/test_defect_models.py
git commit -m "feat: DefectType + DefectRecord models + migration"
```

---

### Task 2: DefectService + 事件

**Files:**
- Create: `src/lightmes/modules/production/defect_service.py`（DefectService: log_defect / handle_rework / handle_scrap / handle_concession）
- Modify: `src/lightmes/modules/production/events.py`（DefectLogged + DefectHandled）
- Test: `tests/modules/production/test_defect_service.py`（新）

**Interfaces:**
- Consumes: `DefectType` + `DefectRecord`（Task 1）、`ReworkService.rework / scrap`（既有）、`SerialUnitRepository`、`MasterDataQueryService`
- Produces:
  - `DefectService.log_defect(defect_type_id, sn, discovered_by, operation_id=None, work_station_id=None, position=None, remark=None) -> DefectRecord`
  - `DefectService.handle_rework(record_id, handled_by, target_seq, expected_repass_station_id, remark=None) -> DefectRecord`
  - `DefectService.handle_scrap(record_id, handled_by, remark=None) -> DefectRecord`
  - `DefectService.handle_concession(record_id, handled_by, remark=None) -> DefectRecord`
  - `DefectLogged` / `DefectHandled` 事件

- [ ] **Step 1: 加事件**

在 `src/lightmes/modules/production/events.py` 末尾加：
```python
@dataclass
class DefectLogged(Event):
    defect_record_id: int
    serial_unit_id: int
    sn: str
    defect_type_code: str
    severity: str


@dataclass
class DefectHandled(Event):
    defect_record_id: int
    serial_unit_id: int
    sn: str
    decision: str  # rework/scrap/concession
```

- [ ] **Step 2: 写 DefectService 失败测试**

创建 `tests/modules/production/test_defect_service.py`：
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db, n_ops=2):
    md = MasterDataService(db)
    user = User(username="dsv", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DSL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="DSW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="DSW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="DSP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id])
        for i in range(n_ops)
    ]
    routing = md.create_routing(RoutingCreate(code="DSRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DSSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DSWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="DSWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="SCRATCH", name="划伤", category="外观", severity="major")
    db.add(dt); db.flush()
    return db, (ws1, ws2), user, wo, su, dt


def test_log_defect_quarantines_sn(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=user.id,
        position="左上角", remark="测试")
    db.refresh(su)
    assert su.status == "quarantined"
    assert rec.defect_type_code == "SCRATCH"
    assert rec.severity == "major"
    assert rec.handling_status == "pending"
    assert rec.position == "左上角"


def test_log_defect_scrapped_sn_rejected(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    from lightmes.modules.trace.rework_service import ReworkService
    ReworkService(db).scrap(su.sn, reason="先报废")
    with pytest.raises(BusinessRuleError, match="已判废"):
        DefectService(db).log_defect(
            defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)


def test_log_defect_quarantined_sn_rejected(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    with pytest.raises(BusinessRuleError, match="已隔离"):
        DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)


def test_handle_rework_calls_rework_service(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    rec = DefectService(db).handle_rework(
        record_id=rec.id, handled_by=user.id,
        target_seq=0, expected_repass_station_id=ws2.id, remark="返工")
    db.refresh(su)
    assert su.status == "reworking"
    assert rec.handling_status == "rework"
    assert rec.handled_by == user.id


def test_handle_scrap_calls_scrap(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    rec = DefectService(db).handle_scrap(record_id=rec.id, handled_by=user.id, remark="报废")
    db.refresh(su)
    assert su.status == "scrapped"
    assert rec.handling_status == "scrap"


def test_handle_concession_back_to_in_process(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    rec = DefectService(db).handle_concession(record_id=rec.id, handled_by=user.id, remark="让步")
    db.refresh(su)
    assert su.status == "in_process"
    assert rec.handling_status == "concession"


def test_handle_already_handled_rejected(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_concession(record_id=rec.id, handled_by=user.id)
    with pytest.raises(BusinessRuleError, match="已处理"):
        DefectService(db).handle_scrap(record_id=rec.id, handled_by=user.id)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_service.py -v
```
Expected: FAIL with ImportError（DefectService 不存在）

- [ ] **Step 4: 实现 DefectService**

创建 `src/lightmes/modules/production/defect_service.py`：
```python
from datetime import datetime
from sqlalchemy.orm import Session

from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.events import DefectLogged, DefectHandled
from lightmes.modules.production.models import DefectType, DefectRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError
from lightmes.shared.events import event_bus


class DefectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.rework = ReworkService(db)
        self.serial_units = SerialUnitRepository(db)

    def log_defect(self, defect_type_id: int, sn: str, discovered_by: int,
                   operation_id: int | None = None, work_station_id: int | None = None,
                   position: str | None = None, remark: str | None = None,
                   ) -> DefectRecord:
        dt = self.db.get(DefectType, defect_type_id)
        if dt is None or not dt.is_active:
            raise NotFoundError(f"缺陷类型不存在或已停用: {defect_type_id}")
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "scrapped":
            raise BusinessRuleError(f"SN 已判废，不可登记缺陷: {sn}")
        if su.status == "quarantined":
            raise BusinessRuleError(f"SN 已隔离，请先处理既有缺陷: {sn}")
        su.status = "quarantined"
        record = DefectRecord(
            defect_type_id=dt.id,
            defect_type_code=dt.code, defect_type_name=dt.name,
            severity=dt.severity,
            serial_unit_id=su.id, work_order_id=su.work_order_id,
            operation_id=operation_id, work_station_id=work_station_id,
            position=position, discovered_by=discovered_by,
            handling_status="pending", remark=remark)
        self.db.add(record)
        self.db.flush()
        event_bus.publish(DefectLogged(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            defect_type_code=dt.code, severity=dt.severity))
        return record

    def _get_pending(self, record_id: int) -> DefectRecord:
        record = self.db.get(DefectRecord, record_id)
        if record is None:
            raise NotFoundError(f"缺陷记录不存在: {record_id}")
        if record.handling_status != "pending":
            raise BusinessRuleError(f"缺陷已处理: {record.handling_status}")
        return record

    def handle_rework(self, record_id: int, handled_by: int,
                      target_seq: int, expected_repass_station_id: int,
                      remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        self.rework.rework(
            sn=su.sn, target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
            operator_id=handled_by)
        record.handling_status = "rework"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="rework"))
        return record

    def handle_scrap(self, record_id: int, handled_by: int,
                     remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        self.rework.scrap(su.sn, reason=remark)
        record.handling_status = "scrap"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="scrap"))
        return record

    def handle_concession(self, record_id: int, handled_by: int,
                          remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        su.status = "in_process"  # 一律回 in_process
        record.handling_status = "concession"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="concession"))
        return record
```

- [ ] **Step 5: 跑测试确认通过**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_service.py -v
```
Expected: 7 PASS

注：`test_log_defect_scrapped_sn_rejected` 和 `test_log_defect_quarantined_sn_rejected` 在 DefectService 实现前就会 raise（因 su.status 检查在 log_defect 内），所以这两个测试在 Step 3 也会 FAIL（ImportError 优先）。

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/defect_service.py src/lightmes/modules/production/events.py tests/modules/production/test_defect_service.py
git commit -m "feat: DefectService (log + handle_rework/scrap/concession) + DefectLogged/Handled events"
```

---

### Task 3: 状态机适配（pass/skip/rework 拒绝/允许 quarantined）

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（pass_operation + skip_operation 步骤 1 拒绝集合加 quarantined）
- Modify: `src/lightmes/modules/trace/rework_service.py`（scrap 允许集合加 quarantined + finished）
- Test: `tests/modules/production/test_defect_state_machine.py`（新）

**Interfaces:**
- Consumes: `SerialUnit.status="quarantined"`（既有字段，新值）
- Produces: pass/skip 拒绝 quarantined；scrap 允许 quarantined + finished

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_defect_state_machine.py`：
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup_passed_sn(db):
    md = MasterDataService(db)
    user = User(username="smop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="SML", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SMW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SMP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SMRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="SMSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SMWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SMWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    return db, ws, user, su


def _set_quarantined(su):
    su.status = "quarantined"


def test_pass_operation_rejects_quarantined(db_session):
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id))


def test_skip_operation_rejects_quarantined(db_session):
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).skip_operation(OperationSkipInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="试图跳"))


def test_rework_allows_quarantined(db_session):
    """rework 仅拒 scrapped，quarantined 天然通过。"""
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    su2 = ReworkService(db).rework(
        sn=su.sn, target_seq=0, expected_repass_station_id=ws.id, operator_id=user.id)
    assert su2.status == "reworking"


def test_scrap_allows_quarantined(db_session):
    db, ws, user, su = _setup_passed_sn(db_session)
    _set_quarantined(su); db.flush()
    su2 = ReworkService(db).scrap(su.sn, reason="隔离后报废")
    assert su2.status == "scrapped"


def test_scrap_allows_finished(db_session):
    """finished 件发现缺陷也能报废。"""
    db, ws, user, su = _setup_passed_sn(db_session)
    # 推进到 finished（2 工序都过站）
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.status == "finished"
    su2 = ReworkService(db).scrap(su.sn, reason="完工后报废")
    assert su2.status == "scrapped"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_state_machine.py -v
```
Expected: pass/skip 拒绝 quarantined 测试 FAIL（当前放行）；scrap quarantined/finished 测试 FAIL（当前拒绝）

- [ ] **Step 3: 改 pass_operation + skip_operation 拒绝集合**

在 `src/lightmes/modules/production/operation_pass_service.py` 找到 `pass_operation` 步骤 1（定位 SN 后的状态检查，约 line 43）：
```python
            if su.status in ("finished", "scrapped"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {su.sn}")
```
改为：
```python
            if su.status in ("finished", "scrapped", "quarantined"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {su.sn}")
```

在 `skip_operation` 方法找到同样的状态检查（约 line 270，结构与 pass 一致），做同样改动。

- [ ] **Step 4: 改 ReworkService.scrap 允许集合**

在 `src/lightmes/modules/trace/rework_service.py` 找到 `scrap` 方法（约 line 95-100）：
```python
        if su.status not in ("in_process", "reworking"):
            raise BusinessRuleError(f"仅在制/返工件可判废，当前: {su.status}")
```
改为：
```python
        if su.status not in ("in_process", "reworking", "quarantined", "finished"):
            raise BusinessRuleError(f"仅在制/返工/隔离/完工件可判废，当前: {su.status}")
```

- [ ] **Step 5: 跑测试确认通过**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_state_machine.py -v
```
Expected: 5 PASS

- [ ] **Step 6: 跑回归**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skip.py tests/modules/production/test_operation_pass_rework_station.py tests/modules/production/test_p2h_e2e.py tests/modules/production/test_first_inspection_e2e.py tests/modules/trace/test_rework_service.py -v
```
Expected: 全绿（既有 SN 不会是 quarantined 状态，扩展拒绝集合不影响既有路径；scrap 扩展允许集合是超集，既有路径不受影响）

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/modules/production/operation_pass_service.py src/lightmes/modules/trace/rework_service.py tests/modules/production/test_defect_state_machine.py
git commit -m "feat: state machine adapts quarantined (pass/skip reject; scrap allows quarantined+finished)"
```

---

### Task 4: 缺陷类型管理 UI（CRUD 页）

**Files:**
- Modify: `src/lightmes/modules/quality/router.py`（新增 defect-types 路由）
- Create: `src/lightmes/templates/quality/defect_types.html`
- Create: `src/lightmes/templates/quality/partials/defect_type_row.html`
- Modify: `src/lightmes/templates/home.html`（质量管理卡片加入口）
- Test: `tests/modules/quality/test_defect_type_pages.py`（新）

**Interfaces:**
- Consumes: `DefectType`（Task 1）
- Produces: `GET /quality/defect-types`、`POST /quality/defect-types`、`POST /quality/defect-types/{id}/delete`

- [ ] **Step 1: 写路由测试**

创建 `tests/modules/quality/test_defect_type_pages.py`：
```python
"""缺陷类型管理页路由测试。Service-level 验证（避免 TestClient DB 隔离问题）。"""
from sqlalchemy import select
from lightmes.modules.production.models import DefectType


def test_defect_type_crud_via_orm(db_session):
    """直接 ORM 验证 DefectType CRUD（路由层只是薄封装）。"""
    dt = DefectType(code="CRACK", name="裂纹", category="外观", severity="critical")
    db_session.add(dt); db_session.flush()
    db_session.refresh(dt)
    assert dt.id is not None
    # 读
    found = db_session.execute(select(DefectType).where(DefectType.code == "CRACK")).scalar_one()
    assert found.name == "裂纹"
    # 软删
    found.is_active = False
    db_session.flush()
    db_session.refresh(found)
    assert found.is_active is False
```

- [ ] **Step 2: 加路由**

在 `src/lightmes/modules/quality/router.py` 末尾加（确认顶部 import 含 `select`、`DefectType`、`_login_guard`）：
```python
from lightmes.modules.production.models import DefectType


@router.get("/quality/defect-types", response_class=HTMLResponse)
def defect_types_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    types = db.execute(
        select(DefectType).order_by(DefectType.id)
    ).scalars().all()
    return templates.TemplateResponse(
        request, "quality/defect_types.html",
        {"types": types})


@router.post("/quality/defect-types", response_class=HTMLResponse)
def defect_type_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    category: str = Form(""),
    severity: str = Form("major"),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    try:
        dt = DefectType(
            code=code, name=name,
            category=category if category else None,
            severity=severity,
            description=description if description else None)
        db.add(dt); db.commit(); db.refresh(dt)
        return templates.TemplateResponse(
            request, "quality/partials/defect_type_row.html",
            {"dt": dt})
    except Exception as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": str(e), "colspan": 6})


@router.post("/quality/defect-types/{dt_id}/delete")
def defect_type_delete(
    request: Request, dt_id: int, db: Session = Depends(get_db),
) -> Response:
    if (r := _login_guard(request, db)): return r
    dt = db.get(DefectType, dt_id)
    if dt:
        dt.is_active = False  # 软删
        db.commit()
    return Response(status_code=303, headers={"Location": "/quality/defect-types"})
```

- [ ] **Step 3: 加模板**

创建 `src/lightmes/templates/quality/defect_types.html`：
```html
{% extends "base.html" %}
{% block title %}缺陷类型管理{% endblock %}
{% block content %}
<h1 class="page-title">缺陷类型管理</h1>

<div class="card">
  <div class="card__title">新增缺陷类型</div>
  <form class="form-row" hx-post="/quality/defect-types" hx-target="#type-list" hx-swap="beforeend">
    <div class="field"><label>编码</label><input name="code" required></div>
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field"><label>分类</label>
      <select name="category">
        <option value="">无</option>
        <option value="外观">外观</option>
        <option value="尺寸">尺寸</option>
        <option value="功能">功能</option>
        <option value="其他">其他</option>
      </select>
    </div>
    <div class="field"><label>严重度</label>
      <select name="severity">
        <option value="minor">minor</option>
        <option value="major" selected>major</option>
        <option value="critical">critical</option>
      </select>
    </div>
    <div class="field" style="flex:1"><label>描述</label><input name="description"></div>
    <button type="submit">新增</button>
  </form>
</div>

<div class="card">
  <div class="card__title">缺陷类型列表</div>
  <table class="data-table">
    <thead><tr><th>编码</th><th>名称</th><th>分类</th><th>严重度</th><th>状态</th><th>操作</th></tr></thead>
    <tbody id="type-list">
      {% for dt in types %}
      {% include "quality/partials/defect_type_row.html" %}
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

创建 `src/lightmes/templates/quality/partials/defect_type_row.html`：
```html
<tr>
  <td>{{ dt.code }}</td>
  <td>{{ dt.name }}</td>
  <td>{{ dt.category or '-' }}</td>
  <td><span class="badge severity-{{ dt.severity }}">{{ dt.severity }}</span></td>
  <td>{% if dt.is_active %}<span class="badge">启用</span>{% else %}<span class="badge" style="background:#999">停用</span>{% endif %}</td>
  <td>
    <form hx-post="/quality/defect-types/{{ dt.id }}/delete" hx-target="closest tr" hx-swap="outerHTML" style="display:inline">
      <button type="submit" class="btn-secondary btn-sm" onclick="return confirm('确定停用？')">停用</button>
    </form>
  </td>
</tr>
```

- [ ] **Step 4: home.html 加入口**

在 `src/lightmes/templates/home.html` 找到质量管理卡片（含首检配置 + 测试数据入口），加：
```html
    <a class="nav-card" href="/quality/defect-types">
      <span class="nav-card__icon">🐞</span>
      <div class="nav-card__name">缺陷类型</div>
      <div class="nav-card__desc">缺陷分类主数据</div>
    </a>
```

- [ ] **Step 5: CSS 加 severity badge**

在 `src/lightmes/static/css/app.css` 末尾加：
```css
.severity-critical { background: #dc3545; color: #fff; }
.severity-major { background: #ffc107; color: #333; }
.severity-minor { background: #6c757d; color: #fff; }
```

- [ ] **Step 6: 跑测试**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/quality/test_defect_type_pages.py -v
```
Expected: 1 PASS

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/modules/quality/router.py src/lightmes/templates/quality/defect_types.html src/lightmes/templates/quality/partials/defect_type_row.html src/lightmes/templates/home.html src/lightmes/static/css/app.css tests/modules/quality/test_defect_type_pages.py
git commit -m "feat: defect type management page (CRUD + soft-delete) + home nav"
```

---

### Task 5: 缺陷登记页 + 路由

**Files:**
- Modify: `src/lightmes/modules/quality/router.py`（新增 /quality/defects/log GET+POST）
- Create: `src/lightmes/templates/quality/defect_log.html`
- Create: `src/lightmes/templates/quality/partials/defect_log_success.html`
- Modify: `src/lightmes/templates/home.html`（加登记入口）
- Test: `tests/modules/quality/test_defect_log_page.py`（新）

**Interfaces:**
- Consumes: `DefectService.log_defect`（Task 2）、`DefectType`（Task 1）
- Produces: `GET /quality/defects/log`、`POST /quality/defects/log`

- [ ] **Step 1: 写测试**

创建 `tests/modules/quality/test_defect_log_page.py`：
```python
"""缺陷登记页 service-level 测试。"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType, DefectRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User


def _setup(db):
    md = MasterDataService(db)
    user = User(username="dlop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DLL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="DLW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="DLP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="DLRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DLSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DLWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="DLWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="STAIN", name="污渍", category="外观", severity="minor")
    db.add(dt); db.flush()
    return ws, user, su, dt


def test_log_defect_full_flow(db_session):
    db = db_session
    ws, user, su, dt = _setup(db)
    rec = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=user.id,
        position="底面", remark="测试登记")
    db.refresh(su)
    assert su.status == "quarantined"
    assert rec.handling_status == "pending"
    assert rec.position == "底面"
```

- [ ] **Step 2: 加路由**

在 `src/lightmes/modules/quality/router.py` 末尾加：
```python
from lightmes.modules.production.defect_service import DefectService


@router.get("/quality/defects/log", response_class=HTMLResponse)
def defect_log_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    sn = request.query_params.get("sn", "")
    types = db.execute(
        select(DefectType).where(DefectType.is_active == True).order_by(DefectType.code)
    ).scalars().all()
    return templates.TemplateResponse(
        request, "quality/defect_log.html",
        {"types": types, "sn": sn})


@router.post("/quality/defects/log", response_class=HTMLResponse)
def defect_log_submit(
    request: Request,
    sn: str = Form(...),
    defect_type_id: int = Form(...),
    position: str = Form(""),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    user = current_user_or_none(request, db)
    try:
        record = DefectService(db).log_defect(
            defect_type_id=defect_type_id, sn=sn, discovered_by=user.id,
            position=position if position else None,
            remark=remark if remark else None)
        db.commit()
    except Exception as e:
        db.rollback()
        types = db.execute(select(DefectType).where(DefectType.is_active == True).order_by(DefectType.code)).scalars().all()
        return templates.TemplateResponse(
            request, "quality/defect_log.html",
            {"types": types, "sn": sn, "error": str(e)})
    return templates.TemplateResponse(
        request, "quality/partials/defect_log_success.html",
        {"record": record})
```

顶部 import 加 `current_user_or_none`：
```python
from lightmes.modules.auth.dependencies import current_user_or_none
```

- [ ] **Step 3: 加模板**

创建 `src/lightmes/templates/quality/defect_log.html`：
```html
{% extends "base.html" %}
{% block title %}缺陷登记{% endblock %}
{% block content %}
<h1 class="page-title">缺陷登记</h1>

{% if error %}
<div class="alert alert--danger">✗ {{ error }}</div>
{% endif %}

<div class="card">
  <div class="card__title">登记新缺陷</div>
  <form hx-post="/quality/defects/log" hx-target="#result" hx-swap="innerHTML">
    <div class="field"><label>成品 SN</label><input name="sn" value="{{ sn }}" required></div>
    <div class="field"><label>缺陷类型</label>
      <select name="defect_type_id" required>
        <option value="">请选择</option>
        {% for t in types %}
        <option value="{{ t.id }}">{{ t.code }} {{ t.name }}（{{ t.severity }}）</option>
        {% endfor %}
      </select>
    </div>
    <div class="field"><label>位置（可选）</label><input name="position" placeholder="如：左上角"></div>
    <div class="field" style="flex:1"><label>备注（可选）</label><input name="remark"></div>
    <button type="submit">登记</button>
  </form>
  <div id="result"></div>
</div>
{% endblock %}
```

创建 `src/lightmes/templates/quality/partials/defect_log_success.html`：
```html
<div class="alert alert--ok">✓ 缺陷 <strong>{{ record.defect_type_code }} {{ record.defect_type_name }}</strong> 已登记（记录 #{{ record.id }}），SN <strong>{{ record.serial_unit_id }}</strong> 已隔离。请前往 <a href="/quality/defects/{{ record.id }}">缺陷详情</a> 处理。</div>
```

- [ ] **Step 4: home.html 加登记入口**

在质量管理卡片加：
```html
    <a class="nav-card" href="/quality/defects/log">
      <span class="nav-card__icon">📝</span>
      <div class="nav-card__name">缺陷登记</div>
      <div class="nav-card__desc">记录发现的不良</div>
    </a>
```

- [ ] **Step 5: 跑测试**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/quality/test_defect_log_page.py -v
```
Expected: 1 PASS

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/quality/router.py src/lightmes/templates/quality/defect_log.html src/lightmes/templates/quality/partials/defect_log_success.html src/lightmes/templates/home.html tests/modules/quality/test_defect_log_page.py
git commit -m "feat: defect logging page + route (creates record + quarantines SN)"
```

---

### Task 6: 缺陷列表 + 详情 + 处理路由（rework/scrap/concession）

**Files:**
- Modify: `src/lightmes/modules/quality/router.py`（新增 defects 列表/详情/3 个 handle 路由 + rework-stations HTMX 路由）
- Create: `src/lightmes/templates/quality/defect_list.html`
- Create: `src/lightmes/templates/quality/defect_detail.html`
- Create: `src/lightmes/templates/quality/partials/rework_stations.html`
- Modify: `src/lightmes/templates/home.html`（加列表入口）
- Test: `tests/modules/quality/test_defect_routes.py`（新）

**Interfaces:**
- Consumes: `DefectService.handle_rework/scrap/concession`（Task 2）、`ReworkService`、`require_role`、routing 工序查询
- Produces: `GET /quality/defects`、`GET /quality/defects/{id}`、`POST /quality/defects/{id}/handle-rework`、`POST /quality/defects/{id}/handle-scrap`、`POST /quality/defects/{id}/handle-concession`、`GET /quality/defects/{id}/rework-stations`

- [ ] **Step 1: 写测试**

创建 `tests/modules/quality/test_defect_routes.py`：
```python
"""缺陷详情处理路由 service-level 测试。"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User


def _setup_with_defect(db):
    md = MasterDataService(db)
    user = User(username="drop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DRL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="DRW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="DRW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="DRP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="DRRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DRSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DRWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="DRWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="FLAW", name="瑕疵", category="外观", severity="major")
    db.add(dt); db.flush()
    record = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    db.flush()
    return (ws1, ws2), user, su, record


def test_handle_rework_via_service(db_session):
    db = db_session
    (ws1, ws2), user, su, record = _setup_with_defect(db)
    rec = DefectService(db).handle_rework(
        record_id=record.id, handled_by=user.id,
        target_seq=0, expected_repass_station_id=ws2.id)
    db.refresh(su)
    assert rec.handling_status == "rework"
    assert su.status == "reworking"


def test_handle_scrap_via_service(db_session):
    db = db_session
    (ws1, ws2), user, su, record = _setup_with_defect(db)
    rec = DefectService(db).handle_scrap(record_id=record.id, handled_by=user.id, remark="报废")
    db.refresh(su)
    assert rec.handling_status == "scrap"
    assert su.status == "scrapped"


def test_handle_concession_via_service(db_session):
    db = db_session
    (ws1, ws2), user, su, record = _setup_with_defect(db)
    rec = DefectService(db).handle_concession(record_id=record.id, handled_by=user.id, remark="让步")
    db.refresh(su)
    assert rec.handling_status == "concession"
    assert su.status == "in_process"
```

- [ ] **Step 2: 加路由**

在 `src/lightmes/modules/quality/router.py` 末尾加（import `WorkOrder`、`Operation`、`WorkStation`、`require_role`、`DefectRecord`）：
```python
from lightmes.modules.auth.dependencies import require_role
from lightmes.modules.production.models import DefectRecord, WorkOrder
from lightmes.modules.masterdata.models import Operation, WorkStation
from lightmes.modules.masterdata.query_service import MasterDataQueryService


@router.get("/quality/defects", response_class=HTMLResponse)
def defect_list_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    status_filter = request.query_params.get("status", "")
    q = select(DefectRecord).order_by(DefectRecord.discovered_at.desc())
    if status_filter:
        q = q.where(DefectRecord.handling_status == status_filter)
    records = db.execute(q).scalars().all()
    return templates.TemplateResponse(
        request, "quality/defect_list.html",
        {"records": records, "status_filter": status_filter})


@router.get("/quality/defects/{record_id}", response_class=HTMLResponse)
def defect_detail_page(request: Request, record_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    record = db.get(DefectRecord, record_id)
    if record is None:
        return Response(status_code=404)
    su = SerialUnitRepository(db).get(record.serial_unit_id)
    wo = db.get(WorkOrder, record.work_order_id)
    # 工序列表（用于返工 target_seq 下拉）
    operations = MasterDataQueryService(db).get_operations(wo.routing_id) if wo else []
    user = current_user_or_none(request, db)
    can_concede = user is not None and user.role_obj is not None and user.role_obj.name in ("admin", "supervisor")
    return templates.TemplateResponse(
        request, "quality/defect_detail.html",
        {"record": record, "su": su, "operations": operations, "can_concede": can_concede})


@router.get("/quality/defects/{record_id}/rework-stations", response_class=HTMLResponse)
def defect_rework_stations(
    request: Request, record_id: int,
    target_seq: int = Query(...), db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX：target_seq 选定后联动站位下拉（复用 P2h _resolve_rework_stations 模式）。"""
    if (r := _login_guard(request, db)): return r
    record = db.get(DefectRecord, record_id)
    if record is None:
        return Response(status_code=404)
    su = SerialUnitRepository(db).get(record.serial_unit_id)
    wo = db.get(WorkOrder, su.work_order_id)
    query = MasterDataQueryService(db)
    operations = query.get_operations(wo.routing_id)
    first_repass_op = next((o for o in operations if o.seq > target_seq), None)
    stations = []
    if first_repass_op:
        allowed = query.get_allowed_work_stations(first_repass_op.id)
        station_ids = [w.id for w in allowed] or [first_repass_op.default_work_station_id]
        stations = list(db.execute(
            select(WorkStation).where(WorkStation.id.in_(station_ids))
        ).scalars().all())
    return templates.TemplateResponse(
        request, "quality/partials/rework_stations.html",
        {"stations": stations, "first_repass_op": first_repass_op})


@router.post("/quality/defects/{record_id}/handle-rework", response_class=HTMLResponse)
def defect_handle_rework(
    request: Request, record_id: int,
    target_seq: int = Form(...),
    expected_repass_station_id: int = Form(...),
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    user = current_user_or_none(request, db)
    try:
        DefectService(db).handle_rework(
            record_id=record_id, handled_by=user.id,
            target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
            remark=remark or None)
        db.commit()
    except Exception as e:
        db.rollback()
        return Response(status_code=422, content=str(e))
    return Response(status_code=303, headers={"Location": f"/quality/defects/{record_id}"})


@router.post("/quality/defects/{record_id}/handle-scrap", response_class=HTMLResponse)
def defect_handle_scrap(
    request: Request, record_id: int,
    remark: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    user = current_user_or_none(request, db)
    try:
        DefectService(db).handle_scrap(
            record_id=record_id, handled_by=user.id, remark=remark or None)
        db.commit()
    except Exception as e:
        db.rollback()
        return Response(status_code=422, content=str(e))
    return Response(status_code=303, headers={"Location": f"/quality/defects/{record_id}"})


@router.post("/quality/defects/{record_id}/handle-concession", response_class=HTMLResponse)
def defect_handle_concession(
    request: Request, record_id: int,
    remark: str = Form(""),
    db: Session = Depends(get_db),
    user = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    try:
        DefectService(db).handle_concession(
            record_id=record_id, handled_by=user.id, remark=remark or None)
        db.commit()
    except Exception as e:
        db.rollback()
        return Response(status_code=422, content=str(e))
    return Response(status_code=303, headers={"Location": f"/quality/defects/{record_id}"})
```

顶部 import 加 `Query`、`SerialUnitRepository`：
```python
from fastapi import APIRouter, Depends, Form, Query, Request
from lightmes.modules.production.repository import SerialUnitRepository
```

- [ ] **Step 3: 加列表模板**

创建 `src/lightmes/templates/quality/defect_list.html`：
```html
{% extends "base.html" %}
{% block title %}缺陷记录{% endblock %}
{% block content %}
<h1 class="page-title">缺陷记录</h1>

<div class="card">
  <div class="card__title">过滤</div>
  <form method="get" class="form-row">
    <div class="field"><label>处理状态</label>
      <select name="status" onchange="this.form.submit()">
        <option value="">全部</option>
        <option value="pending" {% if status_filter == 'pending' %}selected{% endif %}>待处理</option>
        <option value="rework" {% if status_filter == 'rework' %}selected{% endif %}>已返工</option>
        <option value="scrap" {% if status_filter == 'scrap' %}selected{% endif %}>已报废</option>
        <option value="concession" {% if status_filter == 'concession' %}selected{% endif %}>已让步</option>
      </select>
    </div>
  </form>
</div>

<div class="card">
  <div class="card__title">缺陷列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>SN ID</th><th>缺陷类型</th><th>严重度</th><th>发现时间</th><th>处理状态</th></tr></thead>
    <tbody>
      {% for r in records %}
      <tr>
        <td><a href="/quality/defects/{{ r.id }}">#{{ r.id }}</a></td>
        <td>{{ r.serial_unit_id }}</td>
        <td>{{ r.defect_type_code }} {{ r.defect_type_name }}</td>
        <td><span class="badge severity-{{ r.severity }}">{{ r.severity }}</span></td>
        <td>{{ r.discovered_at.strftime('%Y-%m-%d %H:%M') }}</td>
        <td><span class="badge status-{{ r.handling_status }}">{{ r.handling_status }}</span></td>
      </tr>
      {% else %}
      <tr><td colspan="6">无记录</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: 加详情模板**

创建 `src/lightmes/templates/quality/defect_detail.html`：
```html
{% extends "base.html" %}
{% block title %}缺陷详情 #{{ record.id }}{% endblock %}
{% block content %}
<h1 class="page-title">缺陷详情 <small>#{{ record.id }}</small></h1>

<div class="card">
  <div class="card__title">基本信息</div>
  <table class="data-table">
    <tr><th>缺陷类型</th><td>{{ record.defect_type_code }} {{ record.defect_type_name }}</td></tr>
    <tr><th>严重度</th><td><span class="badge severity-{{ record.severity }}">{{ record.severity }}</span></td></tr>
    <tr><th>SN ID</th><td>{{ record.serial_unit_id }}</td></tr>
    <tr><th>SN 状态</th><td>{{ su.status if su else '-' }}</td></tr>
    <tr><th>位置</th><td>{{ record.position or '-' }}</td></tr>
    <tr><th>发现时间</th><td>{{ record.discovered_at.strftime('%Y-%m-%d %H:%M') }}</td></tr>
    <tr><th>处理状态</th><td><span class="badge status-{{ record.handling_status }}">{{ record.handling_status }}</span></td></tr>
    {% if record.handled_at %}
    <tr><th>处理时间</th><td>{{ record.handled_at.strftime('%Y-%m-%d %H:%M') }}</td></tr>
    <tr><th>处理备注</th><td>{{ record.handling_remark or '-' }}</td></tr>
    {% endif %}
    <tr><th>备注</th><td>{{ record.remark or '-' }}</td></tr>
  </table>
</div>

{% if record.handling_status == 'pending' %}
<div class="card">
  <div class="card__title">处理决策</div>

  <details>
    <summary><strong>返工</strong>（SN 回到 target_seq 工序重做）</summary>
    <form hx-post="/quality/defects/{{ record.id }}/handle-rework" class="form-row" style="margin-top:12px"
          onsubmit="return confirm('确认返工？')">
      <div class="field"><label>回退到工序</label>
        <select name="target_seq" required
                hx-get="/quality/defects/{{ record.id }}/rework-stations"
                hx-trigger="change"
                hx-target="#rework-stations"
                hx-swap="innerHTML"
                hx-include="this">
          <option value="">请选择</option>
          {% for op in operations %}
          <option value="{{ op.seq - 1 }}">回退到工序 {{ op.seq - 1 }} 之前（重做 {{ op.code }} {{ op.name }}）</option>
          {% endfor %}
        </select>
      </div>
      <div id="rework-stations"></div>
      <div class="field" style="flex:1"><label>备注</label><input name="remark"></div>
      <button type="submit">确认返工</button>
    </form>
  </details>

  <details>
    <summary><strong>报废</strong>（SN 终态判废）</summary>
    <form method="post" action="/quality/defects/{{ record.id }}/handle-scrap" class="form-row" style="margin-top:12px"
          onsubmit="return confirm('确认报废？此操作不可逆')">
      <div class="field" style="flex:1"><label>备注</label><input name="remark"></div>
      <button type="submit" class="btn-danger">确认报废</button>
    </form>
  </details>

  {% if can_concede %}
  <details>
    <summary><strong>让步接收</strong>（supervisor 授权，SN 回 in_process 继续生产）</summary>
    <form method="post" action="/quality/defects/{{ record.id }}/handle-concession" class="form-row" style="margin-top:12px"
          onsubmit="return confirm('确认让步接收？')">
      <div class="field" style="flex:1"><label>让步理由（必填）</label><input name="remark" required></div>
      <button type="submit">确认让步</button>
    </form>
  </details>
  {% else %}
  <p class="nav-card__desc">让步接收需 supervisor/admin 角色</p>
  {% endif %}
</div>
{% else %}
<div class="alert alert--ok">该缺陷已处理（{{ record.handling_status }}）</div>
{% endif %}

<p><a href="/quality/defects">返回缺陷列表</a></p>
{% endblock %}
```

创建 `src/lightmes/templates/quality/partials/rework_stations.html`：
```html
{% if stations %}
<div class="field"><label>预期返工站位</label>
  <select name="expected_repass_station_id" required>
    <option value="">请选择</option>
    {% for s in stations %}
    <option value="{{ s.id }}">{{ s.name }}</option>
    {% endfor %}
  </select>
</div>
{% if first_repass_op %}
<div class="nav-card__desc">将重做工序 {{ first_repass_op.seq }} {{ first_repass_op.name }}</div>
{% endif %}
{% else %}
<div class="alert alert--danger">该 target_seq 之后无工序可重做</div>
{% endif %}
```

- [ ] **Step 5: home.html + CSS**

home.html 质量管理卡片加：
```html
    <a class="nav-card" href="/quality/defects">
      <span class="nav-card__icon">📋</span>
      <div class="nav-card__name">缺陷记录</div>
      <div class="nav-card__desc">缺陷列表与处理</div>
    </a>
```

app.css 末尾加：
```css
.status-pending { background: #dc3545; color: #fff; }
.status-rework { background: #17a2b8; color: #fff; }
.status-scrap { background: #6c757d; color: #fff; }
.status-concession { background: #28a745; color: #fff; }
```

- [ ] **Step 6: 跑测试**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/quality/test_defect_routes.py -v
```
Expected: 3 PASS

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/modules/quality/router.py src/lightmes/templates/quality/defect_list.html src/lightmes/templates/quality/defect_detail.html src/lightmes/templates/quality/partials/rework_stations.html src/lightmes/templates/home.html src/lightmes/static/css/app.css tests/modules/quality/test_defect_routes.py
git commit -m "feat: defect list + detail + handling routes (rework/scrap/concession) + rework-stations HTMX"
```

---

### Task 7: E2E + 回归

**Files:**
- Test: `tests/modules/production/test_defect_e2e.py`（新）

**Interfaces:**
- Consumes: 全部前序 Task

- [ ] **Step 1: 写 E2E 测试**

创建 `tests/modules/production/test_defect_e2e.py`：
```python
"""缺陷管理 E2E：登记 → 隔离 → 处理（三路）→ 解除。Service-level。"""
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db):
    md = MasterDataService(db)
    user = User(username="e2dm", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="E2DL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="E2DW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="E2DW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="E2DP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="E2DRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="E2DSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="E2DWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="E2DWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="E2SCRATCH", name="划伤", category="外观", severity="major")
    db.add(dt); db.flush()
    return (ws1, ws2), user, su, dt


def test_e2e_log_quarantines_then_pass_blocked(db_session):
    """登记 → 隔离 → 扫码过站被拒。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    db.refresh(su)
    assert su.status == "quarantined"
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))


def test_e2e_concession_unblocks_pass(db_session):
    """登记 → 让步 → 回 in_process → 过站通过。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    record = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_concession(record_id=record.id, handled_by=user.id, remark="让步")
    db.refresh(su)
    assert su.status == "in_process"
    # 过站通过（op2）
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    assert result.passed_op.seq == 2


def test_e2e_scrap_terminal(db_session):
    """登记 → 报废 → 终态 scrapped → 过站拒绝。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    record = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_scrap(record_id=record.id, handled_by=user.id, remark="报废")
    db.refresh(su)
    assert su.status == "scrapped"
    with pytest.raises(BusinessRuleError, match="已scrapped"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))


def test_e2e_rework_then_repass(db_session):
    """登记 → 返工 → reworking → re-pass → in_process。"""
    db = db_session
    (ws1, ws2), user, su, dt = _setup(db)
    record = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_rework(
        record_id=record.id, handled_by=user.id,
        target_seq=0, expected_repass_station_id=ws2.id, remark="返工")
    db.refresh(su)
    assert su.status == "reworking"
    assert su.rework_target_station_id == ws2.id
    # re-pass @ ws2
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    assert result.passed_op.seq == 1
    db.refresh(su)
    assert su.status == "in_process"
    assert su.rework_target_station_id is None  # 首次 re-pass 后清空
```

- [ ] **Step 2: 跑 E2E**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_e2e.py -v
```
Expected: 4 PASS

- [ ] **Step 3: 全量回归（缺陷 + 首检 + P2h + 既有 pass/rework）**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_models.py tests/modules/production/test_defect_service.py tests/modules/production/test_defect_state_machine.py tests/modules/production/test_defect_e2e.py tests/modules/quality/test_defect_type_pages.py tests/modules/quality/test_defect_log_page.py tests/modules/quality/test_defect_routes.py tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skip.py tests/modules/production/test_operation_pass_rework_station.py tests/modules/production/test_operation_pass_first_inspection.py tests/modules/production/test_p2h_e2e.py tests/modules/production/test_first_inspection_e2e.py tests/modules/trace/test_rework_service.py -v
```
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add tests/modules/production/test_defect_e2e.py
git commit -m "test: defect management E2E - log+quarantine+block + 3 handling paths"
```

- [ ] **Step 5: 最终验证**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic check
```
Expected: 无 pending 迁移。

---

## Self-Review

**1. Spec coverage:**
- §1 现状 → Task 1-6 全覆盖
- §2 10 项决策 → 全体现（#6 一律回 in_process 在 Task 2 handle_concession；#8 target_seq 下拉在 Task 6 详情页）
- §3 数据模型 → Task 1 ✓
- §4.1 DefectService → Task 2 ✓
- §4.2 既有服务适配 → Task 3 ✓
- §4.3 新事件 → Task 2 ✓
- §5 路由（11 个）→ Task 4（defect-types 3 个）+ Task 5（defects/log 2 个）+ Task 6（defects list/detail/handle-rework/handle-scrap/handle-concession/rework-stations 6 个）= 11 ✓
- §6 UI 4 页 → Task 4（defect_types）+ Task 5（defect_log）+ Task 6（defect_list + defect_detail）✓
- §7 边界（11 场景）→ Task 2 测试覆盖 scrapped/quarantined 拒绝；Task 3 覆盖状态机；Task 7 E2E 覆盖三路处理
- §8 测试 → 全覆盖
- §9 文件清单 → File Structure 一致

**2. Placeholder scan:**
- 无 "TBD"/"TODO"。
- Task 6 §2 路由代码完整（含 _resolve_rework_stations 模式复用）。
- 模板代码完整。

**3. Type consistency:**
- `DefectService.log_defect` 签名（defect_type_id, sn, discovered_by, operation_id, work_station_id, position, remark）在 Task 2 定义，Task 5 调用一致 ✓
- `handle_rework` 签名（record_id, handled_by, target_seq, expected_repass_station_id, remark）在 Task 2 定义，Task 6 调用一致 ✓
- `handle_scrap` / `handle_concession` 签名一致 ✓
- `DefectLogged` / `DefectHandled` 事件字段一致 ✓
- `DefectType` / `DefectRecord` 字段名（defect_type_code/severity 快照/handling_status 等）跨 Task 一致 ✓
- `SerialUnit.status="quarantined"` 字符串值跨 Task 一致 ✓

**结论**：plan 完整覆盖 spec，类型一致，无严重占位。可执行。
