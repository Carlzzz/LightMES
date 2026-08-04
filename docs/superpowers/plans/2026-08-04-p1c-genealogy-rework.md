# P1c 追溯 + 物料谱系 + 返工 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P1b 过站主线上补齐物料谱系绑定、正/反向追溯、产品履历、返工/拆解，形成完整追溯闭环（P1 MVP 收官）。

**Architecture:** 新增 `trace` 模块（genealogy_bind 模型、GenealogyService 绑定/解绑、TraceService 追溯查询、ReworkService 返工）。过站服务在同一事务内调 GenealogyService 完成绑定（production→trace 单向调用）。masterdata facade 扩展 `get_active_bom`。接入库内事件（过站/绑定/解绑/返工/完工发布；trace 订阅 StationPassed 为 no-op handler）。沿用 P0/P1a/P1b 全部约定。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管）, PostgreSQL+TimescaleDB, pytest, uv。

## Global Constraints

- Python 3.12；依赖用 `uv`（`uv run`）。
- SQLAlchemy 2.0：`Mapped[]`/`mapped_column()`，继承 `lightmes.shared.base.Base`+`TimestampMixin`。
- 所有 schema 变更走 Alembic autogenerate；新模型加到 trace/models.py 并在 `src/lightmes/migrations/env.py` 导入（`from lightmes.modules.trace import models as _trace_models  # noqa: F401`）。
- **跨模块读取只走 facade**：trace 读 masterdata（active BOM）一律通过 `MasterDataQueryService`，禁止在 trace 业务代码 import masterdata repository/models。production 调 trace 只调 `GenealogyService` 公开接口。
- **领域异常**：P1c 新代码抛 `lightmes.shared.errors` 的 `DomainError` 子类（`NotFoundError`/`ConflictError`/`ValidationError`/`BusinessRuleError`），全局 handler 映射。P1a 旧裸 ValueError 不回改。
- **事务边界在 `get_db`（请求级 commit/rollback）；repository 只 `flush()`，不 commit。** 过站+绑定单事务：绑定失败整个过站回滚。
- **HTMX 写处理器吞 DomainError 前必须先 `db.rollback()`**（P1b 确立的约定）再渲染红片段。
- API 端点用 `response_model=` 类型化；写接口加 `current_user: User = Depends(require_login)`；HTMX 页面写操作未登录用 `current_user_or_none` → `Response(status_code=401, headers={"HX-Redirect": "/login"})`。
- 集成测试连真实 PostgreSQL（`db_session` fixture）。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- 事件为 dataclass，继承 `lightmes.shared.events.Event`；用模块级 `event_bus` 发布/订阅。绑定在过站事务内**同步**完成，不靠事件；trace 订阅 StationPassed 为 no-op/日志 handler（证明总线通）。
- HTMX 服务端渲染，模板 `{{ }}` 自动转义；第三方 JS 用 P0 本地 `/static/vendor/htmx.min.js`；无 SPA。
- 提交前缀 `feat:`/`chore:`/`test:`；每 Task 末尾提交。DRY/YAGNI/TDD。
- Shell 用 bash 语法。DB 需 running。
- 追溯查询单层；组件绑定不启用工序级强制（binding_config 仍不用）；返工走原路线；scrap 终态仅 in_process/reworking 可判废。

---

## File Structure

P1c 结束时新增/修改：

```
src/lightmes/modules/masterdata/query_service.py   # 改：加 get_active_bom + get_active_bom_items
src/lightmes/shared/events.py                       # 改：加具体事件 dataclass（或单独 events 定义）
src/lightmes/modules/production/events.py           # 新增：production 领域事件 dataclass
src/lightmes/modules/production/station_pass_service.py  # 改：发布事件 + 集成绑定 + 放开 reworking
src/lightmes/modules/production/schemas.py          # 改：ComponentInput + StationPassInput.components
src/lightmes/modules/trace/__init__.py              # 新增：register(app) + 订阅 StationPassed
src/lightmes/modules/trace/models.py                # 新增：GenealogyBind
src/lightmes/modules/trace/schemas.py               # 新增：绑定/追溯/返工 schema
src/lightmes/modules/trace/repository.py            # 新增：GenealogyBindRepository
src/lightmes/modules/trace/events.py                # 新增：trace 领域事件 dataclass
src/lightmes/modules/trace/genealogy_service.py     # 新增：GenealogyService 绑定/解绑
src/lightmes/modules/trace/trace_service.py         # 新增：TraceService 履历/正查/反查
src/lightmes/modules/trace/rework_service.py        # 新增：ReworkService 返工/判废
src/lightmes/modules/trace/router.py                # 新增：追溯查询页 + 返工页 + API
src/lightmes/main.py                                # 改：注册 trace 模块
src/lightmes/migrations/env.py                      # 改：导入 trace.models
src/lightmes/migrations/versions/<auto>_*.py        # 新增：genealogy_binds 迁移
src/lightmes/templates/production/scan.html         # 改：加组件输入行
src/lightmes/templates/trace/query.html             # 新增：追溯查询页
src/lightmes/templates/trace/rework.html            # 新增：返工页
tests/modules/masterdata/test_query_service_bom.py  # 新增
tests/modules/production/test_events.py             # 新增（事件发布/订阅）
tests/modules/trace/                                # 新增：各 service 测试
```

---

### Task 1: facade 扩展 get_active_bom + active BOM items

给 `MasterDataQueryService` 加读 active BOM 的方法（还 P1b 记的账），供 trace 绑定校验用。`BomRepository` 已有 `get_active_by_product` 和 `items_of`，facade 薄封装。

**Files:**
- Modify: `src/lightmes/modules/masterdata/query_service.py`
- Test: `tests/modules/masterdata/test_query_service_bom.py`

**Interfaces:**
- Consumes: masterdata `Bom`, `BomItem` models, `BomRepository`（模块内部，允许）。
- Produces:
  - `MasterDataQueryService.get_active_bom(product_id: int) -> Bom | None`
  - `MasterDataQueryService.get_active_bom_items(product_id: int) -> list[BomItem]`（该产品 active BOM 的行；无 active BOM → `[]`）

- [ ] **Step 1: 写失败测试**

`tests/modules/masterdata/test_query_service_bom.py`:
```python
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, BomCreate, BomItemCreate,
)


def _fixture(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="QF", name="成品", type="finished"))
    c1 = md.create_product(
        ProductCreate(code="QC1", name="主板", type="component", track_mode="serial"))
    c2 = md.create_product(
        ProductCreate(code="QC2", name="螺丝", type="consumable", track_mode="batch"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1),
        BomItemCreate(component_product_id=c2.id, qty=4),
    ]))
    return fin, c1, c2


def test_get_active_bom(db_session):
    fin, c1, c2 = _fixture(db_session)
    q = MasterDataQueryService(db_session)
    bom = q.get_active_bom(fin.id)
    assert bom is not None
    assert bom.status == "active"


def test_get_active_bom_items(db_session):
    fin, c1, c2 = _fixture(db_session)
    q = MasterDataQueryService(db_session)
    items = q.get_active_bom_items(fin.id)
    comp_ids = {i.component_product_id for i in items}
    assert comp_ids == {c1.id, c2.id}


def test_get_active_bom_items_empty_for_no_bom(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="NOBOM", name="x", type="finished"))
    q = MasterDataQueryService(db_session)
    assert q.get_active_bom(p.id) is None
    assert q.get_active_bom_items(p.id) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_query_service_bom.py -v`
Expected: FAIL —— `AttributeError`（`get_active_bom` 未定义）。

- [ ] **Step 3: 扩展 facade**

在 `src/lightmes/modules/masterdata/query_service.py`：顶部 import 改为
```python
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Bom, BomItem, Product, Routing, RoutingStep
from lightmes.modules.masterdata.repository import BomRepository, RoutingRepository
```
`__init__` 加 `self._boms = BomRepository(db)`，并加方法：
```python
    def get_active_bom(self, product_id: int) -> Bom | None:
        return self._boms.get_active_by_product(product_id)

    def get_active_bom_items(self, product_id: int) -> list[BomItem]:
        bom = self._boms.get_active_by_product(product_id)
        if bom is None:
            return []
        return self._boms.items_of(bom.id)
```

- [ ] **Step 4: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_query_service_bom.py -v` → PASS（3）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata/query_service.py tests/modules/masterdata/test_query_service_bom.py
git commit -m "feat: extend MasterDataQueryService with active BOM reads"
```

---

### Task 2: production 领域事件 + 过站发布 StationPassed/SerialUnitFinished

定义 production 领域事件 dataclass，过站服务在成功后发布。还 P1b 推迟的事件账。绑定/返工事件在后续任务随其服务加入。

**Files:**
- Create: `src/lightmes/modules/production/events.py`
- Modify: `src/lightmes/modules/production/station_pass_service.py`（发布事件）
- Test: `tests/modules/production/test_events.py`

**Interfaces:**
- Consumes: `lightmes.shared.events.Event`, `event_bus`。
- Produces:
  - `production.events.StationPassed`（dataclass(Event)：`serial_unit_id:int`, `sn:str`, `work_order_id:int`, `routing_step_id:int`, `station_id:int`）
  - `production.events.SerialUnitFinished`（dataclass(Event)：`serial_unit_id:int`, `sn:str`, `work_order_id:int`）
  - `StationPassService.pass_station` 在成功路径末尾 publish `StationPassed`（每次成功过站），末站完工时 publish `SerialUnitFinished`。

- [ ] **Step 1: 写事件 dataclass**

`src/lightmes/modules/production/events.py`:
```python
from dataclasses import dataclass
from lightmes.shared.events import Event


@dataclass
class StationPassed(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
    routing_step_id: int
    station_id: int


@dataclass
class SerialUnitFinished(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
```

- [ ] **Step 2: 写失败测试（订阅捕获事件）**

`tests/modules/production/test_events.py`:
```python
from lightmes.shared.events import event_bus
from lightmes.modules.production.events import StationPassed, SerialUnitFinished
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, StationPassInput
from lightmes.modules.production.station_pass_service import StationPassService


def _line(db_session, steps_n=1):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="EV", name="壳", type="finished"))
    stations = [md.create_station(StationCreate(code=f"EVS{i}", name=f"工位{i}"))
                for i in range(steps_n)]
    r = md.create_routing(RoutingCreate(code="EVR", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=i+1, station_id=stations[i].id, name=f"工序{i+1}")
               for i in range(steps_n)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="EVRL", name="r", pattern="E{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="EVWO", product_id=p.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return stations, wo


def test_station_passed_event_published(db_session):
    stations, wo = _line(db_session, steps_n=2)
    captured = []
    event_bus.subscribe(StationPassed, lambda e: captured.append(e))
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=stations[0].id, work_order_code="EVWO"))
    assert any(e.sn == res.sn and e.station_id == stations[0].id for e in captured)


def test_serial_unit_finished_event_published(db_session):
    stations, wo = _line(db_session, steps_n=1)  # 单工序：首站即末站
    captured = []
    event_bus.subscribe(SerialUnitFinished, lambda e: captured.append(e))
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=stations[0].id, work_order_code="EVWO"))
    assert any(e.sn == res.sn for e in captured)
```
注意：`event_bus` 是进程级单例，订阅会累积；测试用 lambda 捕获自身事件、按内容断言，不依赖"仅一次"，避免跨测试串扰。

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_events.py -v`
Expected: FAIL —— ImportError（`production.events` 不存在）或事件未发布（captured 为空）。

- [ ] **Step 4: 过站服务发布事件**

在 `src/lightmes/modules/production/station_pass_service.py`：顶部 import 加
```python
from lightmes.shared.events import event_bus
from lightmes.modules.production.events import StationPassed, SerialUnitFinished
```
在末站完工分支（`if is_last:` 内、设置 finished 之后）加发布 `SerialUnitFinished`：
```python
            event_bus.publish(SerialUnitFinished(
                serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            ))
```
在 `self.db.flush()`（步骤 9 之后、构造返回值之前）之后加发布 `StationPassed`：
```python
        event_bus.publish(StationPassed(
            serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            routing_step_id=expected.id, station_id=data.station_id,
        ))
```
说明：事件在事务内 flush 之后 publish（同步分发）；MVP 订阅方无副作用，无一致性风险。

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_events.py -v` → PASS（2）。
全量回归 → 全绿（注意：全局 event_bus 订阅在其他测试不影响，因发布的事件类型独立）。
```bash
git add src/lightmes/modules/production/events.py src/lightmes/modules/production/station_pass_service.py tests/modules/production/test_events.py
git commit -m "feat: publish StationPassed and SerialUnitFinished events on station pass"
```

---

### Task 3: trace 模块脚手架 + GenealogyBind 模型 + 迁移 + repository + StationPassed 订阅

建立 `trace` 模块骨架，落地 `GenealogyBind` 模型、repository，注册进 app，并订阅 `StationPassed` 为 no-op/日志 handler（证明总线通，还 P1b 账）。

**Files:**
- Create: `src/lightmes/modules/trace/__init__.py`, `models.py`, `repository.py`
- Create: `tests/modules/trace/__init__.py`, `tests/modules/trace/test_genealogy_bind_model.py`
- Modify: `src/lightmes/main.py`（注册 trace）、`src/lightmes/migrations/env.py`（导入 trace.models）
- Create: `src/lightmes/migrations/versions/<auto>_create_genealogy_bind.py`

**Interfaces:**
- Consumes: `Base`/`TimestampMixin`；FK 指向 serial_units/products/station_passes/users；`event_bus`、`production.events.StationPassed`。
- Produces:
  - `trace.models.GenealogyBind`（表 `genealogy_binds`）：`id:int PK`, `parent_sn_id:int FK serial_units.id`, `component_product_id:int FK products.id`, `component_type:str`, `component_sn:str|None`（索引）, `component_batch_no:str|None`（索引）, `qty:Numeric(12,3) default 1`, `bind_time:datetime tz-aware server_default now()`, `operator_id:int|None FK users.id`, `station_pass_id:int|None FK station_passes.id`, `status:str default "active"`, `unbind_time:datetime|None`, `unbind_reason:str|None`, + timestamps。
  - `trace.repository.GenealogyBindRepository(db)`：`add(bind)->GenealogyBind`, `list_active_by_parent(parent_sn_id)->list[GenealogyBind]`, `list_by_parent(parent_sn_id)->list[GenealogyBind]`（含历史）, `list_active_by_component_sn(component_sn)->list[GenealogyBind]`, `list_by_component_sn(component_sn)->list`, `list_by_component_batch(batch_no)->list`, `get(id)->GenealogyBind|None`。
  - `trace.register(app)`：`app.include_router(router)` + `event_bus.subscribe(StationPassed, _on_station_passed)`（no-op/日志 handler）。

- [ ] **Step 1: trace 模块骨架 + 模型**

`src/lightmes/modules/trace/__init__.py`:
```python
import logging
from fastapi import FastAPI
from lightmes.shared.events import event_bus

logger = logging.getLogger("lightmes.trace")


def _on_station_passed(event) -> None:
    # MVP: 仅记录，证明事件总线连通；绑定在过站事务内同步完成，不靠本订阅。
    logger.debug("trace observed StationPassed: sn=%s", getattr(event, "sn", None))


def register(app: FastAPI) -> None:
    from lightmes.modules.trace.router import router
    from lightmes.modules.production.events import StationPassed

    app.include_router(router)
    event_bus.subscribe(StationPassed, _on_station_passed)
```

`src/lightmes/modules/trace/models.py`:
```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class GenealogyBind(Base, TimestampMixin):
    __tablename__ = "genealogy_binds"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_sn_id: Mapped[int] = mapped_column(
        ForeignKey("serial_units.id"), index=True
    )
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    component_type: Mapped[str] = mapped_column()  # serial/batch
    component_sn: Mapped[str | None] = mapped_column(index=True, default=None)
    component_batch_no: Mapped[str | None] = mapped_column(index=True, default=None)
    qty: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    bind_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    station_pass_id: Mapped[int | None] = mapped_column(
        ForeignKey("station_passes.id"), default=None
    )
    status: Mapped[str] = mapped_column(default="active")  # active/unbound
    unbind_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    unbind_reason: Mapped[str | None] = mapped_column(default=None)
```

`tests/modules/trace/__init__.py`: 空文件。

- [ ] **Step 2: 导入 model 到 env + 迁移**

在 `src/lightmes/migrations/env.py` 追加：
```python
from lightmes.modules.trace import models as _trace_models  # noqa: F401
```
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create genealogy_bind"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移仅创建 `genealogy_binds`（含 FK 与 parent_sn_id/component_sn/component_batch_no 索引）。确认无 spurious 操作（元数据已在 P1b 修正，autogenerate 不应再误删部分索引；若出现任何删除他表索引/表的操作，停止并报告）。

- [ ] **Step 3: 写 repository**

`src/lightmes/modules/trace/repository.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.trace.models import GenealogyBind


class GenealogyBindRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, bind: GenealogyBind) -> GenealogyBind:
        self.db.add(bind)
        self.db.flush()
        return bind

    def get(self, id: int) -> GenealogyBind | None:
        return self.db.get(GenealogyBind, id)

    def list_active_by_parent(self, parent_sn_id: int) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(
                GenealogyBind.parent_sn_id == parent_sn_id,
                GenealogyBind.status == "active",
            )
        ).scalars().all())

    def list_by_parent(self, parent_sn_id: int) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(GenealogyBind.parent_sn_id == parent_sn_id)
        ).scalars().all())

    def list_active_by_component_sn(self, component_sn: str) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(
                GenealogyBind.component_sn == component_sn,
                GenealogyBind.status == "active",
            )
        ).scalars().all())

    def list_by_component_sn(self, component_sn: str) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(GenealogyBind.component_sn == component_sn)
        ).scalars().all())

    def list_by_component_batch(self, batch_no: str) -> list[GenealogyBind]:
        return list(self.db.execute(
            select(GenealogyBind).where(GenealogyBind.component_batch_no == batch_no)
        ).scalars().all())
```

- [ ] **Step 4: 写空 router 并注册模块**

`src/lightmes/modules/trace/router.py`:
```python
from fastapi import APIRouter

router = APIRouter()
# 追溯查询 + 返工 端点在后续任务加入
```
在 `src/lightmes/main.py`：import 加 `from lightmes.modules import trace`（与现有 `auth, masterdata, production` 同行或新增），在 `production.register(app)` 下方加 `trace.register(app)`。

- [ ] **Step 5: 写测试**

`tests/modules/trace/test_genealogy_bind_model.py`:
```python
from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository


def _finished_sn(db_session):
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    from lightmes.modules.production.models import SerialUnit
    from lightmes.modules.production.repository import SerialUnitRepository
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="GBP", name="壳", type="finished"))
    s = md.create_station(StationCreate(code="GBS", name="工位"))
    r = md.create_routing(RoutingCreate(code="GBR", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="GBWO", product_id=p.id, routing_id=r.id, qty=5))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="GBSN1", work_order_id=wo.id, product_id=p.id))
    return su, p


def test_genealogy_bind_persist_and_query(db_session):
    su, p = _finished_sn(db_session)
    repo = GenealogyBindRepository(db_session)
    b = repo.add(GenealogyBind(
        parent_sn_id=su.id, component_product_id=p.id,
        component_type="serial", component_sn="COMP-1",
    ))
    assert b.id is not None
    assert b.status == "active"
    assert [x.id for x in repo.list_active_by_parent(su.id)] == [b.id]
    assert [x.id for x in repo.list_active_by_component_sn("COMP-1")] == [b.id]


def test_unbound_excluded_from_active_queries(db_session):
    su, p = _finished_sn(db_session)
    repo = GenealogyBindRepository(db_session)
    b = repo.add(GenealogyBind(
        parent_sn_id=su.id, component_product_id=p.id,
        component_type="batch", component_batch_no="LOT-9", status="unbound",
    ))
    assert repo.list_active_by_parent(su.id) == []
    assert [x.id for x in repo.list_by_parent(su.id)] == [b.id]  # 历史仍在
```

- [ ] **Step 6: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_genealogy_bind_model.py -v` → PASS（2）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/trace src/lightmes/main.py src/lightmes/migrations tests/modules/trace
git commit -m "feat: add trace module with GenealogyBind model and StationPassed subscriber"
```

---

### Task 4: GenealogyService 绑定/解绑（自由绑定 + 类型校验 + 唯一件占用反查）

trace 的核心绑定逻辑：校验组件属于成品 active BOM、按 track_mode 校验 SN/批次、唯一件占用反查，写 genealogy_bind 并发 GenealogyBound；解绑发 GenealogyUnbound。

**Files:**
- Create: `src/lightmes/modules/trace/events.py`, `src/lightmes/modules/trace/schemas.py`, `src/lightmes/modules/trace/genealogy_service.py`
- Test: `tests/modules/trace/test_genealogy_service.py`

**Interfaces:**
- Consumes: `MasterDataQueryService`（active BOM）、`GenealogyBindRepository`、`SerialUnit`（读 parent，用 production 的 SerialUnitRepository 或直接 db.get？——trace 读 production 的 serial_unit 属跨模块；MVP 允许 trace 通过参数接收已加载的 parent SerialUnit 对象，由调用方(过站服务)传入，避免 trace 反向依赖 production repository）、`event_bus`、`shared.errors`。
- Produces:
  - `trace.events.GenealogyBound`（dataclass(Event)：`parent_sn_id:int`, `component_product_id:int`, `component_type:str`, `component_ref:str`）
  - `trace.events.GenealogyUnbound`（dataclass(Event)：`bind_id:int`, `parent_sn_id:int`, `reason:str|None`）
  - `trace.schemas.ComponentBind`（Pydantic：`component_product_id:int`, `component_sn:str|None=None`, `component_batch_no:str|None=None`, `qty:float=1`）
  - `trace.genealogy_service.GenealogyService(db)`：
    - `bind_components(parent_su, components: list[ComponentBind], operator_id: int | None, station_pass_id: int | None) -> list[GenealogyBind]`（parent_su 是已加载的 SerialUnit；逐项校验+写；发 GenealogyBound）
    - `unbind(bind_id: int, reason: str | None, operator_id: int | None) -> GenealogyBind`（置 unbound + 时间/原因；发 GenealogyUnbound；非 active → BusinessRuleError；不存在 → NotFoundError）

- [ ] **Step 1: 写 events + schemas**

`src/lightmes/modules/trace/events.py`:
```python
from dataclasses import dataclass
from lightmes.shared.events import Event


@dataclass
class GenealogyBound(Event):
    parent_sn_id: int
    component_product_id: int
    component_type: str
    component_ref: str  # component_sn 或 component_batch_no


@dataclass
class GenealogyUnbound(Event):
    bind_id: int
    parent_sn_id: int
    reason: str | None
```

`src/lightmes/modules/trace/schemas.py`:
```python
from pydantic import BaseModel


class ComponentBind(BaseModel):
    component_product_id: int
    component_sn: str | None = None
    component_batch_no: str | None = None
    qty: float = 1
```

- [ ] **Step 2: 写失败测试**

`tests/modules/trace/test_genealogy_service.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import WorkOrderCreate
from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import BusinessRuleError, ValidationError, ConflictError, NotFoundError


def _setup(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="GF", name="成品", type="finished"))
    c_ser = md.create_product(
        ProductCreate(code="GCS", name="主板", type="component", track_mode="serial"))
    c_bat = md.create_product(
        ProductCreate(code="GCB", name="螺丝", type="consumable", track_mode="batch"))
    other = md.create_product(
        ProductCreate(code="GX", name="不在BOM", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id, qty=1),
        BomItemCreate(component_product_id=c_bat.id, qty=4),
    ]))
    s = md.create_station(StationCreate(code="GS", name="工位"))
    r = md.create_routing(RoutingCreate(code="GR", name="路线", product_id=fin.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="GWO", product_id=fin.id, routing_id=r.id, qty=10))
    def make_su(sn):
        return SerialUnitRepository(db_session).add(
            SerialUnit(sn=sn, work_order_id=wo.id, product_id=fin.id))
    return fin, c_ser, c_bat, other, make_su


def test_bind_serial_and_batch(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    su = make_su("F1")
    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_ser.id, component_sn="MB-1"),
        ComponentBind(component_product_id=c_bat.id, component_batch_no="LOT-1", qty=4),
    ], operator_id=None, station_pass_id=None)
    assert len(binds) == 2
    types = {b.component_type for b in binds}
    assert types == {"serial", "batch"}


def test_bind_component_not_in_bom_rejected(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    su = make_su("F2")
    svc = GenealogyService(db_session)
    with pytest.raises(BusinessRuleError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=other.id, component_sn="X-1")],
            operator_id=None, station_pass_id=None)


def test_serial_component_requires_sn(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    su = make_su("F3")
    svc = GenealogyService(db_session)
    with pytest.raises(ValidationError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=c_ser.id)],  # 缺 sn
            operator_id=None, station_pass_id=None)


def test_batch_component_requires_batch_no(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    su = make_su("F4")
    svc = GenealogyService(db_session)
    with pytest.raises(ValidationError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=c_bat.id)],  # 缺 batch_no
            operator_id=None, station_pass_id=None)


def test_unique_component_occupancy_rejected(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    su1 = make_su("F5")
    su2 = make_su("F6")
    svc = GenealogyService(db_session)
    svc.bind_components(su1, [
        ComponentBind(component_product_id=c_ser.id, component_sn="MB-DUP")],
        operator_id=None, station_pass_id=None)
    with pytest.raises(ConflictError):
        svc.bind_components(su2, [
            ComponentBind(component_product_id=c_ser.id, component_sn="MB-DUP")],
            operator_id=None, station_pass_id=None)


def test_unbind(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    su = make_su("F7")
    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_bat.id, component_batch_no="LOT-7")],
        operator_id=None, station_pass_id=None)
    unbound = svc.unbind(binds[0].id, reason="返工换料", operator_id=None)
    assert unbound.status == "unbound"
    assert unbound.unbind_reason == "返工换料"
    with pytest.raises(BusinessRuleError):
        svc.unbind(binds[0].id, reason="再次", operator_id=None)  # 已 unbound


def test_unbind_unknown_rejected(db_session):
    fin, c_ser, c_bat, other, make_su = _setup(db_session)
    svc = GenealogyService(db_session)
    with pytest.raises(NotFoundError):
        svc.unbind(999999, reason=None, operator_id=None)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_genealogy_service.py -v`
Expected: FAIL —— ImportError（`genealogy_service` 不存在）。

- [ ] **Step 4: 写 GenealogyService**

`src/lightmes/modules/trace/genealogy_service.py`:
```python
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.trace.events import GenealogyBound, GenealogyUnbound
from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import (
    NotFoundError, BusinessRuleError, ValidationError, ConflictError,
)
from lightmes.shared.events import event_bus


class GenealogyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.binds = GenealogyBindRepository(db)

    def bind_components(
        self, parent_su, components: list[ComponentBind],
        operator_id: int | None, station_pass_id: int | None,
    ) -> list[GenealogyBind]:
        items = self.query.get_active_bom_items(parent_su.product_id)
        if not items:
            raise BusinessRuleError("成品无 active BOM，无法绑定组件")
        bom_by_component = {i.component_product_id: i for i in items}
        result: list[GenealogyBind] = []
        for comp in components:
            item = bom_by_component.get(comp.component_product_id)
            if item is None:
                raise BusinessRuleError(
                    f"组件不属于本产品 BOM: {comp.component_product_id}")
            track = item.track_mode
            if track == "serial":
                if not comp.component_sn:
                    raise ValidationError("唯一件组件必须提供 component_sn")
                occupied = self.binds.list_active_by_component_sn(comp.component_sn)
                if occupied:
                    raise ConflictError(
                        f"该唯一件已装配在其他成品上: {comp.component_sn}")
            elif track == "batch":
                if not comp.component_batch_no:
                    raise ValidationError("批次件组件必须提供 component_batch_no")
            bind = self.binds.add(GenealogyBind(
                parent_sn_id=parent_su.id,
                component_product_id=comp.component_product_id,
                component_type=track,
                component_sn=comp.component_sn,
                component_batch_no=comp.component_batch_no,
                qty=comp.qty,
                operator_id=operator_id,
                station_pass_id=station_pass_id,
                status="active",
            ))
            event_bus.publish(GenealogyBound(
                parent_sn_id=parent_su.id,
                component_product_id=comp.component_product_id,
                component_type=track,
                component_ref=comp.component_sn or comp.component_batch_no or "",
            ))
            result.append(bind)
        return result

    def unbind(
        self, bind_id: int, reason: str | None, operator_id: int | None,
    ) -> GenealogyBind:
        bind = self.binds.get(bind_id)
        if bind is None:
            raise NotFoundError(f"谱系绑定不存在: {bind_id}")
        if bind.status != "active":
            raise BusinessRuleError(f"绑定非 active，不可解绑: {bind_id}")
        bind.status = "unbound"
        bind.unbind_time = datetime.now(timezone.utc)
        bind.unbind_reason = reason
        self.db.flush()
        event_bus.publish(GenealogyUnbound(
            bind_id=bind.id, parent_sn_id=bind.parent_sn_id, reason=reason,
        ))
        return bind
```
说明：`parent_su` 由调用方（过站服务）传入已加载的 SerialUnit，trace 不反向依赖 production repository。唯一件占用反查用 `list_active_by_component_sn`（active 绑定即视为占用）。

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_genealogy_service.py -v` → PASS（7）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/trace/events.py src/lightmes/modules/trace/schemas.py src/lightmes/modules/trace/genealogy_service.py tests/modules/trace/test_genealogy_service.py
git commit -m "feat: add GenealogyService bind/unbind with BOM and occupancy checks"
```

---

### Task 5: 过站集成绑定（StationPassInput.components + 事务内绑定 + 放开 reworking）

过站时一起扫组件：扩展输入、在过站事务内调 GenealogyService 绑定（绑定失败整个过站回滚）；放开 reworking 状态 SN 过站并复位 in_process。

**Files:**
- Modify: `src/lightmes/modules/production/schemas.py`（ComponentInput + StationPassInput.components + StationPassResult.bound_count）
- Modify: `src/lightmes/modules/production/station_pass_service.py`（调 bind + 放开 reworking）
- Test: `tests/modules/production/test_station_pass_binding.py`

**Interfaces:**
- Consumes: `trace.genealogy_service.GenealogyService`, `trace.schemas.ComponentBind`。
- Produces:
  - `production.schemas.ComponentInput`（Pydantic：`component_product_id:int`, `component_sn:str|None=None`, `component_batch_no:str|None=None`, `qty:float=1`）
  - `StationPassInput` 加字段 `components: list[ComponentInput] = []`
  - `StationPassResult` 加字段 `bound_count: int = 0`（本次绑定的组件数）
  - pass_station：写 station_pass 后拿到其 id，若 `data.components` 非空 → 调 `GenealogyService(db).bind_components(su, [ComponentBind(...)...], operator_id, station_pass.id)`；绑定在同一事务；reworking 的 SN 允许过站，成功后复位 in_process。

- [ ] **Step 1: 加 schemas 字段**

在 `src/lightmes/modules/production/schemas.py`：
```python
class ComponentInput(BaseModel):
    component_product_id: int
    component_sn: str | None = None
    component_batch_no: str | None = None
    qty: float = 1
```
`StationPassInput` 加：`components: list[ComponentInput] = []`。
`StationPassResult` 加：`bound_count: int = 0`。
（`ComponentInput` 放在 `StationPassInput` 定义之前。）

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_station_pass_binding.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput, ComponentInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.shared.errors import BusinessRuleError


def _line(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BF", name="成品", type="finished"))
    comp = md.create_product(
        ProductCreate(code="BC", name="螺丝", type="consumable", track_mode="batch"))
    other = md.create_product(
        ProductCreate(code="BX", name="非BOM件", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=comp.id, qty=4)]))
    s1 = md.create_station(StationCreate(code="BS1", name="上料"))
    s2 = md.create_station(StationCreate(code="BS2", name="装配"))
    r = md.create_routing(RoutingCreate(code="BR", name="路线", product_id=fin.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="BRL", name="r", pattern="B{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="BWO", product_id=fin.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, comp, other, s1, s2, wo


def test_pass_with_component_binds(db_session):
    fin, comp, other, s1, s2, wo = _line(db_session)
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(
        station_id=s1.id, work_order_code="BWO",
        components=[ComponentInput(component_product_id=comp.id,
                                   component_batch_no="LOT-1", qty=4)]))
    assert res.bound_count == 1
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    binds = GenealogyBindRepository(db_session).list_active_by_parent(su.id)
    assert len(binds) == 1
    assert binds[0].component_batch_no == "LOT-1"


def test_bad_component_rolls_back_whole_pass(db_session):
    fin, comp, other, s1, s2, wo = _line(db_session)
    svc = StationPassService(db_session)
    # other 不在 BOM → 绑定失败 → 整个过站回滚，不应留下 SerialUnit
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(
            station_id=s1.id, work_order_code="BWO",
            components=[ComponentInput(component_product_id=other.id,
                                       component_sn="X-1")]))
    # 关键断言：过站被拒后无残留 SerialUnit（同事务回滚）
    assert SerialUnitRepository(db_session).list_by_work_order(wo.id) == []


def test_pass_without_components_bound_count_zero(db_session):
    fin, comp, other, s1, s2, wo = _line(db_session)
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="BWO"))
    assert res.bound_count == 0
```
注意：`test_bad_component_rolls_back_whole_pass` 里，`bind_components` 抛异常传播出 pass_station；因为整个操作在一个 db_session 事务里，服务层不 catch，异常冒泡后调用方（这里是测试直接调 service）——但 service 未 commit，且抛出后测试用同一 session 查到无残留。注意：service 内 SN 已 flush，但抛异常后未 commit；同 session 内后续查询仍可能看到未回滚的 flush。为让断言成立，pass_station 在捕获到绑定异常时应显式 `self.db.rollback()` 再重新抛出，确保 flush 的 SerialUnit 被撤销。见 Step 4。

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pass_binding.py -v`
Expected: FAIL —— `ComponentInput` 未定义 / 绑定未接入。

- [ ] **Step 4: 接入绑定 + 放开 reworking**

在 `src/lightmes/modules/production/station_pass_service.py`：顶部 import 加
```python
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
```
改造步骤 7 的过站写入：让 `passes.add(...)` 的返回值可用，并在乐观锁更新成功、`refresh(su)` 之后、末站完工判定之前插入绑定。将现有：
```python
        self.passes.add(StationPass(
            serial_unit_id=su.id, work_order_id=wo.id,
            routing_step_id=expected.id, station_id=data.station_id,
            operator_id=data.operator_id, result="pass",
        ))
```
改为：
```python
        station_pass = self.passes.add(StationPass(
            serial_unit_id=su.id, work_order_id=wo.id,
            routing_step_id=expected.id, station_id=data.station_id,
            operator_id=data.operator_id, result="pass",
        ))
```
在 `self.db.refresh(su)`（乐观锁更新后）之后、`# 8. 末站完工` 之前插入绑定块：
```python
        # 7b. 组件绑定（同事务；失败则回滚整个过站，含已生成的 SN）
        bound_count = 0
        if data.components:
            try:
                binds = GenealogyService(self.db).bind_components(
                    su,
                    [ComponentBind(
                        component_product_id=c.component_product_id,
                        component_sn=c.component_sn,
                        component_batch_no=c.component_batch_no,
                        qty=c.qty,
                    ) for c in data.components],
                    operator_id=data.operator_id,
                    station_pass_id=station_pass.id,
                )
            except Exception:
                self.db.rollback()
                raise
            bound_count = len(binds)
```
在末尾构造 `StationPassResult(...)` 时加 `bound_count=bound_count`。
放开 reworking：步骤 9"翻转工单为在制"附近，加对 su 的复位——把现有：
```python
        # 9. 翻转工单为在制
        if wo.status == "released":
            wo.status = "in_process"
```
改为：
```python
        # 9. 翻转工单为在制 + 返工件复位
        if wo.status == "released":
            wo.status = "in_process"
        if su.status == "reworking":
            su.status = "in_process"
```
说明：步骤 1 对 SN 的拒绝已只含 `finished`/`scrapped`（不含 reworking），故 reworking 的 SN 本就能进入过站流程；此处成功后复位 in_process。绑定失败时显式 `rollback()` 撤销同事务内已 flush 的 SerialUnit/StationPass，保证"过站被拒无残留"。

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pass_binding.py -v` → PASS（3）。
全量回归 → 全绿（含 P1b 过站测试不受影响：无 components 时 bound_count=0，行为不变）。
```bash
git add src/lightmes/modules/production/schemas.py src/lightmes/modules/production/station_pass_service.py tests/modules/production/test_station_pass_binding.py
git commit -m "feat: integrate component binding into station pass (atomic), open rework re-pass"
```

---

### Task 6: TraceService 履历 / 正向查 / 反向查（单层）

追溯查询：产品履历（过站时间线 + 谱系）、正向（成品→组件）、反向（组件→成品，召回关键）。单层。

**Files:**
- Modify: `src/lightmes/modules/trace/schemas.py`（查询结果 schema）
- Create: `src/lightmes/modules/trace/trace_service.py`
- Test: `tests/modules/trace/test_trace_service.py`

**Interfaces:**
- Consumes: `GenealogyBindRepository`, `production.repository.StationPassRepository`, `production.repository.SerialUnitRepository`（trace 读 production 的过站/SN：通过 production repository 读属跨模块——MVP 允许 trace 直接用 production 的 repository 做只读查询，因追溯本质是读 production 的过站数据；不经 facade 是因为 production 尚无 query facade。记为可接受的读耦合）。
- Produces:
  - `trace.schemas.BindView`（`component_product_id:int`, `component_type:str`, `component_ref:str`, `qty:float`, `status:str`）
  - `trace.schemas.PassView`（`routing_step_id:int`, `station_id:int`, `result:str`, `pass_time:datetime`）
  - `trace.schemas.GenealogyView`（`sn:str`, `components:list[BindView]`）
  - `trace.schemas.HistoryView`（`sn:str`, `passes:list[PassView]`, `components:list[BindView]`）
  - `trace.schemas.ParentRef`（`parent_sn_id:int`, `component_ref:str`, `status:str`）
  - `trace.trace_service.TraceService(db)`：
    - `genealogy_of(sn: str, include_unbound: bool = False) -> GenealogyView`（成品→组件；SN 不存在 → NotFoundError）
    - `where_used(component_sn=None, component_batch_no=None) -> list[ParentRef]`（组件→成品；两者都空 → ValidationError）
    - `history_of(sn: str) -> HistoryView`（过站时间线 + 全部谱系含历史）

- [ ] **Step 1: 加查询 schemas**

在 `src/lightmes/modules/trace/schemas.py` 追加（顶部确保 `from datetime import datetime`）：
```python
class BindView(BaseModel):
    component_product_id: int
    component_type: str
    component_ref: str
    qty: float
    status: str


class PassView(BaseModel):
    routing_step_id: int
    station_id: int
    result: str
    pass_time: datetime


class GenealogyView(BaseModel):
    sn: str
    components: list[BindView]


class HistoryView(BaseModel):
    sn: str
    passes: list[PassView]
    components: list[BindView]


class ParentRef(BaseModel):
    parent_sn_id: int
    component_ref: str
    status: str
```

- [ ] **Step 2: 写失败测试**

`tests/modules/trace/test_trace_service.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput, ComponentInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.modules.trace.trace_service import TraceService
from lightmes.shared.errors import NotFoundError, ValidationError


def _pass_with_components(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="TF", name="成品", type="finished"))
    c = md.create_product(
        ProductCreate(code="TC", name="主板", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c.id, qty=1)]))
    s = md.create_station(StationCreate(code="TS", name="装配"))
    r = md.create_routing(RoutingCreate(code="TR", name="路线", product_id=fin.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="TRL", name="r", pattern="T{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="TWO", product_id=fin.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    res = StationPassService(db_session).pass_station(StationPassInput(
        station_id=s.id, work_order_code="TWO",
        components=[ComponentInput(component_product_id=c.id, component_sn="MB-100")]))
    return res.sn


def test_genealogy_forward(db_session):
    sn = _pass_with_components(db_session)
    view = TraceService(db_session).genealogy_of(sn)
    assert view.sn == sn
    assert len(view.components) == 1
    assert view.components[0].component_ref == "MB-100"


def test_where_used_reverse(db_session):
    sn = _pass_with_components(db_session)
    parents = TraceService(db_session).where_used(component_sn="MB-100")
    assert len(parents) == 1
    assert parents[0].status == "active"


def test_history_includes_passes_and_components(db_session):
    sn = _pass_with_components(db_session)
    h = TraceService(db_session).history_of(sn)
    assert h.sn == sn
    assert len(h.passes) == 1
    assert len(h.components) == 1


def test_genealogy_unknown_sn(db_session):
    with pytest.raises(NotFoundError):
        TraceService(db_session).genealogy_of("NOPE")


def test_where_used_requires_a_key(db_session):
    with pytest.raises(ValidationError):
        TraceService(db_session).where_used()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_trace_service.py -v`
Expected: FAIL —— ImportError（`trace_service` 不存在）。

- [ ] **Step 4: 写 TraceService**

`src/lightmes/modules/trace/trace_service.py`:
```python
from sqlalchemy.orm import Session

from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository,
)
from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.modules.trace.schemas import (
    BindView, PassView, GenealogyView, HistoryView, ParentRef,
)
from lightmes.shared.errors import NotFoundError, ValidationError


def _bind_view(b: GenealogyBind) -> BindView:
    return BindView(
        component_product_id=b.component_product_id,
        component_type=b.component_type,
        component_ref=b.component_sn or b.component_batch_no or "",
        qty=float(b.qty),
        status=b.status,
    )


class TraceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.binds = GenealogyBindRepository(db)
        self.serial_units = SerialUnitRepository(db)
        self.passes = StationPassRepository(db)

    def genealogy_of(self, sn: str, include_unbound: bool = False) -> GenealogyView:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        binds = (self.binds.list_by_parent(su.id) if include_unbound
                 else self.binds.list_active_by_parent(su.id))
        return GenealogyView(sn=sn, components=[_bind_view(b) for b in binds])

    def where_used(
        self, component_sn: str | None = None, component_batch_no: str | None = None,
    ) -> list[ParentRef]:
        if not component_sn and not component_batch_no:
            raise ValidationError("需提供 component_sn 或 component_batch_no")
        if component_sn:
            binds = self.binds.list_by_component_sn(component_sn)
        else:
            binds = self.binds.list_by_component_batch(component_batch_no)
        return [
            ParentRef(
                parent_sn_id=b.parent_sn_id,
                component_ref=b.component_sn or b.component_batch_no or "",
                status=b.status,
            )
            for b in binds
        ]

    def history_of(self, sn: str) -> HistoryView:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        passes = self.passes.list_by_serial_unit(su.id)
        binds = self.binds.list_by_parent(su.id)
        return HistoryView(
            sn=sn,
            passes=[PassView(
                routing_step_id=p.routing_step_id, station_id=p.station_id,
                result=p.result, pass_time=p.pass_time,
            ) for p in passes],
            components=[_bind_view(b) for b in binds],
        )
```

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_trace_service.py -v` → PASS（5）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/trace/schemas.py src/lightmes/modules/trace/trace_service.py tests/modules/trace/test_trace_service.py
git commit -m "feat: add TraceService for history, forward and reverse genealogy queries"
```

---

### Task 7: ReworkService 返工 / 判废

返工：回退到指定工序 seq + 可选解绑组件；判废：置 scrapped 终态。返工后 SN 由扫码工位重新过站（Task 5 已放开 reworking）。

**Files:**
- Create: `src/lightmes/modules/trace/rework_service.py`
- Modify: `src/lightmes/modules/trace/events.py`（加 SerialUnitReworkStarted）
- Test: `tests/modules/trace/test_rework_service.py`

**Interfaces:**
- Consumes: `production.repository.SerialUnitRepository`（读/更新 serial_unit——rework 改 production 的 serial_unit 属跨模块写；MVP 允许 trace 通过 production repository 更新 serial_unit 状态，因返工本质是对在制品状态的操作。记为可接受耦合，若日后收紧再引入 production 的命令 facade）、`GenealogyService`（解绑）、`GenealogyBindRepository`、`event_bus`、`shared.errors`。
- Produces:
  - `trace.events.SerialUnitReworkStarted`（dataclass(Event)：`serial_unit_id:int`, `sn:str`, `target_seq:int`）
  - `trace.rework_service.ReworkService(db)`：
    - `rework(sn: str, target_seq: int, unbind_bind_ids: list[int] = [], reason: str | None = None, operator_id: int | None = None) -> SerialUnit`
    - `scrap(sn: str, reason: str | None = None) -> SerialUnit`

- [ ] **Step 1: 加 SerialUnitReworkStarted 事件**

在 `src/lightmes/modules/trace/events.py` 追加：
```python
@dataclass
class SerialUnitReworkStarted(Event):
    serial_unit_id: int
    sn: str
    target_seq: int
```

- [ ] **Step 2: 写失败测试**

`tests/modules/trace/test_rework_service.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput, ComponentInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.shared.errors import NotFoundError, BusinessRuleError, ValidationError


def _two_step_line(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="RF", name="成品", type="finished"))
    comp = md.create_product(
        ProductCreate(code="RC", name="螺丝", type="consumable", track_mode="batch"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=comp.id, qty=4)]))
    s1 = md.create_station(StationCreate(code="RS1", name="上料"))
    s2 = md.create_station(StationCreate(code="RS2", name="装配"))
    r = md.create_routing(RoutingCreate(code="RR", name="路线", product_id=fin.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRL", name="r", pattern="R{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWO", product_id=fin.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return fin, comp, s1, s2, wo


def test_rework_rolls_back_step_and_status(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.current_step_seq == 1
    reworked = ReworkService(db_session).rework(res.sn, target_seq=0, reason="上料错误")
    assert reworked.status == "reworking"
    assert reworked.current_step_seq == 0


def test_rework_unbinds_components(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(
        station_id=s1.id, work_order_code="RWO",
        components=[ComponentInput(component_product_id=comp.id,
                                   component_batch_no="LOT-1", qty=4)]))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    bind = GenealogyBindRepository(db_session).list_active_by_parent(su.id)[0]
    ReworkService(db_session).rework(res.sn, target_seq=0,
                                     unbind_bind_ids=[bind.id], reason="换料")
    assert GenealogyBindRepository(db_session).list_active_by_parent(su.id) == []


def test_rework_then_repass_resets_in_process(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    ReworkService(db_session).rework(res.sn, target_seq=0)
    # 重新过首站：reworking → in_process
    r2 = pass_svc.pass_station(StationPassInput(station_id=s1.id, sn=res.sn))
    su = SerialUnitRepository(db_session).get_by_sn(res.sn)
    assert su.status == "in_process"
    assert su.current_step_seq == 1


def test_rework_target_seq_must_be_less(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    with pytest.raises(ValidationError):
        ReworkService(db_session).rework(res.sn, target_seq=5)  # >= current


def test_scrap_terminal(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    pass_svc = StationPassService(db_session)
    res = pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="RWO"))
    scrapped = ReworkService(db_session).scrap(res.sn, reason="报废")
    assert scrapped.status == "scrapped"
    # scrapped 后不可过站
    with pytest.raises(BusinessRuleError):
        pass_svc.pass_station(StationPassInput(station_id=s2.id, sn=res.sn))


def test_rework_unknown_sn(db_session):
    fin, comp, s1, s2, wo = _two_step_line(db_session)
    with pytest.raises(NotFoundError):
        ReworkService(db_session).rework("NOPE", target_seq=0)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_rework_service.py -v`
Expected: FAIL —— ImportError（`rework_service` 不存在）。

- [ ] **Step 4: 写 ReworkService**

`src/lightmes/modules/trace/rework_service.py`:
```python
from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.events import SerialUnitReworkStarted
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.shared.errors import (
    NotFoundError, BusinessRuleError, ValidationError, ConflictError,
)
from lightmes.shared.events import event_bus


class ReworkService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.genealogy = GenealogyService(db)

    def rework(
        self, sn: str, target_seq: int, unbind_bind_ids: list[int] | None = None,
        reason: str | None = None, operator_id: int | None = None,
    ) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "scrapped":
            raise BusinessRuleError(f"SN 已判废，不可返工: {sn}")
        if target_seq < 0 or target_seq >= su.current_step_seq:
            raise ValidationError(
                f"返工目标工序 {target_seq} 必须小于当前 {su.current_step_seq}")
        for bind_id in (unbind_bind_ids or []):
            bind = self.genealogy.binds.get(bind_id)
            if bind is None or bind.parent_sn_id != su.id:
                raise NotFoundError(f"谱系绑定不存在或不属于本 SN: {bind_id}")
            self.genealogy.unbind(bind_id, reason=reason, operator_id=operator_id)
        prev_version = su.version
        result = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(status="reworking", current_step_seq=target_seq,
                    version=prev_version + 1)
        )
        if result.rowcount == 0:
            raise ConflictError("该产品正被其他操作处理，请重试")
        self.db.refresh(su)
        event_bus.publish(SerialUnitReworkStarted(
            serial_unit_id=su.id, sn=su.sn, target_seq=target_seq,
        ))
        return su

    def scrap(self, sn: str, reason: str | None = None) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status not in ("in_process", "reworking"):
            raise BusinessRuleError(f"仅在制/返工件可判废，当前: {su.status}")
        su.status = "scrapped"
        self.db.flush()
        return su
```
说明：解绑复用 `GenealogyService.unbind`（会校验 active + 发 GenealogyUnbound）。乐观锁沿用 serial_unit.version。`scrap` 限 in_process/reworking。

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_rework_service.py -v` → PASS（6）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/trace/rework_service.py src/lightmes/modules/trace/events.py tests/modules/trace/test_rework_service.py
git commit -m "feat: add ReworkService for rework rollback and scrap"
```

---

### Task 8: HTMX 页面（追溯查询页 + 返工页 + API）

给 trace 加追溯查询页（正/反查 + 履历）、返工操作页，及类型化 API。写处理器遵守"吞异常前 rollback"。扫码页的组件输入行留作可选增强（本任务聚焦追溯/返工页；扫码绑定已可经 API 用）。

**Files:**
- Modify: `src/lightmes/modules/trace/router.py`（追溯/返工 API + 页面）
- Create: `src/lightmes/templates/trace/query.html`, `src/lightmes/templates/trace/rework.html`
- Test: `tests/modules/trace/test_trace_pages.py`

**Interfaces:**
- Consumes: `TraceService`, `ReworkService`, `require_login`, `current_user_or_none`, `Jinja2Templates`（同其他模块 router：`Path(__file__).resolve().parent.parent.parent / "templates"`）、`shared.errors.DomainError`。
- Produces:
  - `GET /api/trace/genealogy/{sn}` → `GenealogyView`（require_login）
  - `GET /api/trace/where-used?component_sn=&component_batch_no=` → `list[ParentRef]`（require_login）
  - `POST /api/trace/rework`（body: sn/target_seq/unbind_bind_ids/reason）→ `serial_unit` 摘要（require_login）
  - `GET /trace/query` → 查询页（HTMX）
  - `POST /trace/query`（form: query_type, value）→ 结果片段（正查/反查/履历）
  - `GET /trace/rework` → 返工页；`POST /trace/rework`（form）→ 结果片段（未登录 401+HX-Redirect；DomainError → rollback + 红片段）

- [ ] **Step 1: 写模板**

`src/lightmes/templates/trace/query.html`:
```html
{% extends "base.html" %}
{% block title %}追溯查询{% endblock %}
{% block content %}
<h1>追溯查询</h1>
<form hx-post="/trace/query" hx-target="#result" hx-swap="innerHTML">
  <select name="query_type">
    <option value="genealogy">正向: 成品SN→组件</option>
    <option value="where_used_sn">反向: 组件SN→成品</option>
    <option value="where_used_batch">反向: 组件批次→成品</option>
    <option value="history">履历: 成品SN</option>
  </select>
  <input name="value" placeholder="输入 SN 或批次号" required>
  <button type="submit">查询</button>
</form>
<div id="result"></div>
{% endblock %}
```

`src/lightmes/templates/trace/rework.html`:
```html
{% extends "base.html" %}
{% block title %}返工{% endblock %}
{% block content %}
<h1>返工 / 拆解</h1>
<form hx-post="/trace/rework" hx-target="#result" hx-swap="innerHTML">
  <input name="sn" placeholder="成品SN" required>
  <input name="target_seq" type="number" placeholder="回退到工序序号" required>
  <input name="reason" placeholder="返工原因">
  <button type="submit">返工</button>
</form>
<div id="result"></div>
{% endblock %}
```

- [ ] **Step 2: 写失败测试**

`tests/modules/trace/test_trace_pages.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput, ComponentInput,
)
from lightmes.modules.production.station_pass_service import StationPassService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="tr", password="pw12345", display_name="Tr"))
    db_session.flush()
    assert client.post("/login", data={"username": "tr", "password": "pw12345"}).status_code == 200


def _passed_sn(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="PF", name="成品", type="finished"))
    c = md.create_product(
        ProductCreate(code="PC", name="主板", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c.id, qty=1)]))
    s = md.create_station(StationCreate(code="PS", name="装配"))
    r = md.create_routing(RoutingCreate(code="PR", name="路线", product_id=fin.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRL", name="r", pattern="P{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PWO", product_id=fin.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    res = StationPassService(db_session).pass_station(StationPassInput(
        station_id=s.id, work_order_code="PWO",
        components=[ComponentInput(component_product_id=c.id, component_sn="MB-7")]))
    return res.sn


def test_query_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/trace/query")
    assert resp.status_code == 200
    assert "追溯查询" in resp.text


def test_query_forward_genealogy(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.post("/trace/query", data={"query_type": "genealogy", "value": sn})
    assert resp.status_code == 200
    assert "MB-7" in resp.text


def test_query_reverse_where_used(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.post("/trace/query",
        data={"query_type": "where_used_sn", "value": "MB-7"})
    assert resp.status_code == 200
    assert sn in resp.text


def test_api_genealogy_requires_login(client, db_session):
    resp = client.get("/api/trace/genealogy/ANY")
    assert resp.status_code == 401


def test_api_where_used(client, db_session):
    sn = _passed_sn(db_session)
    _login(client, db_session)
    resp = client.get("/api/trace/where-used", params={"component_sn": "MB-7"})
    assert resp.status_code == 200
    assert any(p["component_ref"] == "MB-7" for p in resp.json())


def test_rework_page_requires_login(client, db_session):
    resp = client.post("/trace/rework",
        data={"sn": "X", "target_seq": 0, "reason": ""})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_trace_pages.py -v`
Expected: FAIL（路由未定义 → 404/401 不符）。

- [ ] **Step 4: 写 router**

`src/lightmes/modules/trace/router.py`（覆盖 Task 3 的占位）:
```python
from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_login, current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.modules.trace.schemas import GenealogyView, ParentRef
from lightmes.modules.trace.trace_service import TraceService
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/api/trace/genealogy/{sn}", response_model=GenealogyView)
def api_genealogy(
    sn: str, db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> GenealogyView:
    return TraceService(db).genealogy_of(sn)


@router.get("/api/trace/where-used", response_model=list[ParentRef])
def api_where_used(
    component_sn: str | None = None, component_batch_no: str | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(require_login),
) -> list[ParentRef]:
    return TraceService(db).where_used(
        component_sn=component_sn, component_batch_no=component_batch_no)


@router.get("/trace/query", response_class=HTMLResponse)
def query_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/query.html")


@router.post("/trace/query", response_class=HTMLResponse)
def query_submit(
    request: Request, query_type: str = Form(...), value: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # 手写片段：所有插值一律经 markupsafe.escape() 防 XSS。
    svc = TraceService(db)
    try:
        if query_type == "genealogy":
            view = svc.genealogy_of(value)
            rows = "".join(
                f"<li>{escape(c.component_type)}: {escape(c.component_ref)} "
                f"x{c.qty} [{escape(c.status)}]</li>"
                for c in view.components)
            html = f"<p>成品 {escape(view.sn)} 组件:</p><ul>{rows}</ul>"
        elif query_type == "where_used_sn":
            parents = svc.where_used(component_sn=value)
            rows = "".join(
                f"<li>成品 #{p.parent_sn_id} ({escape(p.component_ref)}) "
                f"[{escape(p.status)}]</li>"
                for p in parents)
            html = f"<p>组件 {escape(value)} 装入:</p><ul>{rows}</ul>"
        elif query_type == "where_used_batch":
            parents = svc.where_used(component_batch_no=value)
            rows = "".join(
                f"<li>成品 #{p.parent_sn_id} ({escape(p.component_ref)}) "
                f"[{escape(p.status)}]</li>"
                for p in parents)
            html = f"<p>批次 {escape(value)} 装入:</p><ul>{rows}</ul>"
        else:  # history
            h = svc.history_of(value)
            passes = "".join(
                f"<li>工序#{p.routing_step_id} 工位#{p.station_id} "
                f"{escape(p.result)} {p.pass_time}</li>"
                for p in h.passes)
            comps = "".join(
                f"<li>{escape(c.component_ref)} [{escape(c.status)}]</li>"
                for c in h.components)
            html = f"<p>SN {escape(h.sn)} 履历:</p><ul>{passes}</ul><p>组件:</p><ul>{comps}</ul>"
    except DomainError as e:
        return HTMLResponse(f'<div style="color:red">✗ {escape(e.detail)}</div>')
    return HTMLResponse(html)


@router.get("/trace/rework", response_class=HTMLResponse)
def rework_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/rework.html")


@router.post("/trace/rework", response_class=HTMLResponse)
def rework_submit(
    request: Request, sn: str = Form(...), target_seq: int = Form(...),
    reason: str = Form(""), db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        su = ReworkService(db).rework(
            sn, target_seq=target_seq, reason=reason or None, operator_id=user.id)
    except DomainError as e:
        db.rollback()  # 吞异常前回滚（P1b 确立的约定）
        return HTMLResponse(f'<div style="color:red">✗ {escape(e.detail)}</div>')
    return HTMLResponse(
        f'<div style="color:green">✓ {escape(su.sn)} '
        f'已返工至工序 {su.current_step_seq}</div>')
```

说明：手写 HTML 片段的每个插值都经 `markupsafe.escape()` 防 XSS。返工页 POST 遵守"未登录 401+HX-Redirect、DomainError 先 `db.rollback()` 再红片段"。`markupsafe` 是 Jinja2 依赖，已在环境中。

- [ ] **Step 5: 运行测试确认通过 + 全量回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_trace_pages.py -v` → PASS（6）。
全量：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。
```bash
git add src/lightmes/modules/trace/router.py src/lightmes/templates/trace tests/modules/trace/test_trace_pages.py
git commit -m "feat: add trace query and rework HTMX pages with typed APIs"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P1c spec §4/§5/§6/§7）：
- genealogy_bind 模型 + 迁移 → Task 3 ✅
- facade get_active_bom → Task 1 ✅
- GenealogyService 绑定（BOM 校验/唯一件占用/类型）+ 解绑 → Task 4 ✅
- 过站集成绑定（同事务、放开 reworking）→ Task 5 ✅
- TraceService 履历/正查/反查（单层）→ Task 6 ✅
- ReworkService 返工/判废 → Task 7 ✅
- 事件接入（StationPassed/SerialUnitFinished/GenealogyBound/GenealogyUnbound/SerialUnitReworkStarted）→ Task 2（过站）/4（绑定/解绑）/7（返工）✅
- trace 订阅 StationPassed no-op → Task 3 ✅
- HTMX 追溯/返工页 → Task 8 ✅

**跨模块耦合记录**（供终审关注）：
- trace→masterdata：走 facade（干净）✅
- production→trace：过站调 `GenealogyService`（调 service 公开接口）✅
- trace→production：TraceService/ReworkService 直接用 production 的 `SerialUnitRepository`/`StationPassRepository` 做读/状态更新。这是有意的"读/命令耦合"——production 尚无 query/command facade，追溯与返工本质操作 production 数据。记为可接受的 MVP 取舍；若日后收紧，引入 production 的 facade。这是本计划最该被终审审视的架构点。

**占位符扫描**：无 TBD/TODO；每个代码步骤含完整代码。Task 8 router 直接 `return HTMLResponse(...)`，所有手写片段插值经 `markupsafe.escape()` 防 XSS。

**类型一致性**：`get_active_bom`/`get_active_bom_items`、`ComponentInput`/`ComponentBind`、`StationPassInput.components`/`StationPassResult.bound_count`、`GenealogyService.bind_components/unbind`、`TraceService.genealogy_of/where_used/history_of`、`ReworkService.rework/scrap`、事件 dataclass —— 定义处与引用处一致 ✅。

**XSS**：Task 8 手写 HTML 片段的所有插值经 `markupsafe.escape()`；这是本计划唯一用手写片段（非 Jinja 模板）的地方，已强制转义。其余页面走 Jinja `{{ }}` 自动转义。
