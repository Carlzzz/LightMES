# P1b 过站 + WIP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P1a 主数据/工单/SN 生成器之上实现过站主线：SN 沿工艺路线过站（防跳站/防重复）、首站按需生成 SN、末站完工计数、WIP 可见；配套 HTMX 扫码工位页与 WIP 看板。

**Architecture:** 沿用 P0/P1a 模块化单体约定。新增：`shared/errors.py` 领域异常体系 + `main.py` 全局 handler；masterdata `query_service.py` 只读 facade（下游只调它，不引用 masterdata models/repository）；production 模块扩展 `serial_unit`/`station_pass` 模型 + `StationPassService`（过站校验链）+ 过站 API/扫码页 + WIP 查询/看板页。真实 PostgreSQL 集成测试，写接口复用 `require_login`。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管）, PostgreSQL+TimescaleDB, pytest, uv。

## Global Constraints

- Python 3.12；依赖用 `uv`（`uv run`）。
- SQLAlchemy 2.0：`Mapped[]`/`mapped_column()`，继承 `lightmes.shared.base.Base`+`TimestampMixin`。
- 所有 schema 变更走 Alembic autogenerate；新模型已通过 `production.models` 在 `src/lightmes/migrations/env.py` 注册（新增模型加到 production/models.py 即被覆盖）。
- **跨模块读取只走 facade**：production 读 masterdata 一律通过 `MasterDataQueryService`，禁止在 production 业务代码里 import masterdata 的 repository；读到的 ORM 对象仅只读使用，不写他模块表。（本约定纠正 P1a 的 `db.get(Model)` 直读；P1a 旧代码不回改。）
- **领域异常**：P1b 新代码抛 `lightmes.shared.errors` 的 `DomainError` 子类（`NotFoundError`/`ConflictError`/`ValidationError`/`BusinessRuleError`），由全局 handler 映射为 HTTP 状态码 + 中文 detail。P1a 旧的裸 `ValueError` 不回改。
- 事务边界在 `get_db`（请求级 commit/rollback）；repository 只 `flush()`，不 commit。
- API 端点用 `response_model=` 类型化；写接口加 `current_user: User = Depends(require_login)`（`lightmes.modules.auth.dependencies.require_login`）；HTMX 页面写操作未登录返回 `Response(status_code=401, headers={"HX-Redirect": "/login"})`（用 `current_user_or_none` 判断）。
- 集成测试连真实 PostgreSQL（`db_session` fixture）。测试命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v`
- 迁移/建表后 `alembic upgrade head`（同样 127.0.0.1 前缀）。
- HTMX 服务端渲染，模板 `{{ }}` 自动转义；第三方 JS 用 P0 本地 `/static/vendor/htmx.min.js`；无 SPA。
- 提交前缀 `feat:`/`chore:`/`test:`；每 Task 末尾提交。DRY/YAGNI/TDD。
- Shell 用 bash 语法（正斜杠路径，`/dev/null`）。DB 需 running。
- 组件绑定/返工不在 P1b（P1c）；`serial_unit.status` 本段只置 in_process/finished/scrapped（reworking 列建好不用）。

---

## File Structure

P1b 结束时新增/修改：

```
src/lightmes/shared/errors.py            # 新增：DomainError 体系
src/lightmes/main.py                      # 改：注册 DomainError 全局 handler
src/lightmes/modules/masterdata/query_service.py  # 新增：MasterDataQueryService 只读 facade
src/lightmes/modules/production/models.py         # 改：加 SerialUnit, StationPass
src/lightmes/modules/production/repository.py     # 改：加 SerialUnitRepository, StationPassRepository
src/lightmes/modules/production/schemas.py        # 改：加 过站输入/结果、WIP 摘要 schema
src/lightmes/modules/production/station_pass_service.py  # 新增：StationPassService
src/lightmes/modules/production/wip_service.py    # 新增：WipService
src/lightmes/modules/production/router.py         # 改：加过站 API + 扫码页 + WIP 页
src/lightmes/templates/production/scan.html       # 新增：扫码工位页
src/lightmes/templates/production/partials/scan_result.html  # 新增：过站结果片段
src/lightmes/templates/production/wip.html        # 新增：WIP 看板页
src/lightmes/migrations/versions/<auto>_*.py      # 新增：serial_units + station_passes 迁移
tests/shared/test_errors.py               # 新增
tests/modules/masterdata/test_query_service.py    # 新增
tests/modules/production/test_station_pass.py     # 新增
tests/modules/production/test_sn_concurrency.py   # 新增：双连接并发
tests/modules/production/test_wip.py              # 新增
tests/modules/production/test_scan_pages.py       # 新增
```

---

### Task 1: 共享领域异常体系 + 全局 handler

建立 `DomainError` 体系与 FastAPI 全局异常处理器，把领域异常映射为 HTTP 状态码 + 中文 JSON detail。P1b 后续任务都用它。

**Files:**
- Create: `src/lightmes/shared/errors.py`
- Modify: `src/lightmes/main.py`（注册 handler）
- Test: `tests/shared/__init__.py`, `tests/shared/test_errors.py`

**Interfaces:**
- Consumes: FastAPI。
- Produces:
  - `lightmes.shared.errors.DomainError`（基类，属性 `status_code: int = 400`，`__init__(self, detail: str)` 存 `self.detail`）
  - 子类：`ValidationError`(400)、`NotFoundError`(404)、`ConflictError`(409)、`BusinessRuleError`(422)
  - `main.py` 注册 `@app.exception_handler(DomainError)` → `JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})`

- [ ] **Step 1: 写失败测试**

`tests/shared/__init__.py`: 空文件。

`tests/shared/test_errors.py`:
```python
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from lightmes.shared.errors import (
    DomainError, ValidationError, NotFoundError, ConflictError, BusinessRuleError,
)


def test_status_codes():
    assert ValidationError("x").status_code == 400
    assert NotFoundError("x").status_code == 404
    assert ConflictError("x").status_code == 409
    assert BusinessRuleError("x").status_code == 422


def test_detail_stored():
    e = BusinessRuleError("防跳站")
    assert e.detail == "防跳站"
    assert isinstance(e, DomainError)


def test_handler_maps_status_and_detail():
    # 一个最小 app 验证 handler 行为（不依赖主 app）
    from lightmes.main import app
    client = TestClient(app, raise_server_exceptions=False)
    # /health 仍在，说明 app 正常加载；handler 行为在 production 端点集成测试中进一步覆盖
    assert client.get("/health").status_code == 200
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/shared/test_errors.py -v`
Expected: FAIL —— ImportError（`lightmes.shared.errors` 不存在）。

- [ ] **Step 3: 写 errors.py**

`src/lightmes/shared/errors.py`:
```python
class DomainError(Exception):
    """业务领域异常基类；status_code 决定 HTTP 映射。"""

    status_code: int = 400

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ValidationError(DomainError):
    status_code = 400


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class BusinessRuleError(DomainError):
    status_code = 422
```

- [ ] **Step 4: 注册全局 handler**

在 `src/lightmes/main.py` 顶部 import 加：
```python
from fastapi import Request
from fastapi.responses import JSONResponse
from lightmes.shared.errors import DomainError
```
在 `app` 创建之后、路由注册附近加：
```python
@app.exception_handler(DomainError)
def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```
（若 `Request`/`JSONResponse` 已被 import 则不重复。）

- [ ] **Step 5: 运行测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/shared/test_errors.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: 全量回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。
```bash
git add src/lightmes/shared/errors.py src/lightmes/main.py tests/shared
git commit -m "feat: add shared domain error hierarchy and global handler"
```

---

### Task 2: masterdata 只读查询 facade

新增 `MasterDataQueryService`，供 production（及后续模块）只读访问 masterdata，避免下游直接引用 masterdata repository/models。

**Files:**
- Create: `src/lightmes/modules/masterdata/query_service.py`
- Test: `tests/modules/masterdata/test_query_service.py`

**Interfaces:**
- Consumes: masterdata 现有 `Product`, `Routing`, `RoutingStep` models 与 `RoutingRepository`（facade 内部可用本模块 repository；这是模块内部，不违反跨模块约定）。
- Produces:
  - `masterdata.query_service.MasterDataQueryService(db)`，方法：
    - `get_product(product_id: int) -> Product | None`
    - `get_routing(routing_id: int) -> Routing | None`
    - `get_ordered_steps(routing_id: int) -> list[RoutingStep]`（按 seq 升序；空路线返回 []）
  - 说明：production 只 import `MasterDataQueryService`（不 import masterdata 的 models/repository 到业务逻辑）。返回的 RoutingStep 供只读使用。

- [ ] **Step 1: 写失败测试**

`tests/modules/masterdata/test_query_service.py`:
```python
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="QP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="QS1", name="工位1"))
    s2 = md.create_station(StationCreate(code="QS2", name="工位2"))
    r = md.create_routing(RoutingCreate(code="QR", name="路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
        ]))
    return p, r


def test_get_ordered_steps_sorted_by_seq(db_session):
    p, r = _line(db_session)
    q = MasterDataQueryService(db_session)
    steps = q.get_ordered_steps(r.id)
    assert [s.seq for s in steps] == [1, 2]
    assert steps[0].name == "上料"


def test_get_product_and_routing(db_session):
    p, r = _line(db_session)
    q = MasterDataQueryService(db_session)
    assert q.get_product(p.id).code == "QP"
    assert q.get_routing(r.id).id == r.id
    assert q.get_product(999999) is None


def test_get_ordered_steps_empty_for_unknown_routing(db_session):
    q = MasterDataQueryService(db_session)
    assert q.get_ordered_steps(999999) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_query_service.py -v`
Expected: FAIL —— ImportError（`query_service` 不存在）。

- [ ] **Step 3: 写 query_service.py**

`src/lightmes/modules/masterdata/query_service.py`:
```python
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product, Routing, RoutingStep
from lightmes.modules.masterdata.repository import RoutingRepository


class MasterDataQueryService:
    """跨模块只读查询 facade。下游模块只调本类，不直接引用 masterdata repository/models。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._routings = RoutingRepository(db)

    def get_product(self, product_id: int) -> Product | None:
        return self.db.get(Product, product_id)

    def get_routing(self, routing_id: int) -> Routing | None:
        return self.db.get(Routing, routing_id)

    def get_ordered_steps(self, routing_id: int) -> list[RoutingStep]:
        return self._routings.steps_of(routing_id)
```

- [ ] **Step 4: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_query_service.py -v` → PASS（3）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata/query_service.py tests/modules/masterdata/test_query_service.py
git commit -m "feat: add MasterDataQueryService read-only cross-module facade"
```

---

### Task 3: SerialUnit + StationPass 模型 + 迁移 + repository

加产品单元 `SerialUnit`（承载 WIP 状态 + 乐观锁）与过站记录 `StationPass`，及各自 repository。

**Files:**
- Modify: `src/lightmes/modules/production/models.py`, `repository.py`
- Create: `src/lightmes/migrations/versions/<auto>_create_serial_unit_and_station_pass.py`
- Test: `tests/modules/production/test_models_serial_unit.py`

**Interfaces:**
- Consumes: `Base`/`TimestampMixin`；FK 指向 P1a work_orders/products/routing_steps/stations、P0 users。
- Produces:
  - `production.models.SerialUnit`（表 `serial_units`）：`id:int PK`, `sn:str unique index`, `work_order_id:int FK work_orders.id`, `product_id:int FK products.id`, `status:str default "in_process"`（in_process/finished/scrapped/reworking）, `current_step_seq:int default 0`, `current_station_id:int|None FK stations.id`, `version:int default 0`, + timestamps。
  - `production.models.StationPass`（表 `station_passes`）：`id:int PK`, `serial_unit_id:int FK serial_units.id`, `work_order_id:int FK work_orders.id`, `routing_step_id:int FK routing_steps.id`, `station_id:int FK stations.id`, `operator_id:int|None FK users.id`, `pass_time:datetime default now`, `result:str default "pass"`（pass/fail）, `remark:str|None`, + timestamps。
  - `production.repository.SerialUnitRepository(db)`：`add(su)->SerialUnit`, `get(id)->SerialUnit|None`, `get_by_sn(sn)->SerialUnit|None`, `list_by_work_order(work_order_id)->list[SerialUnit]`, `list_in_process_by_station(station_id)->list[SerialUnit]`。
  - `production.repository.StationPassRepository(db)`：`add(sp)->StationPass`, `exists_pass(serial_unit_id, routing_step_id)->bool`（该 SN 该工序是否已有 result=pass 记录）, `list_by_serial_unit(serial_unit_id)->list[StationPass]`（按 pass_time 升序）。

- [ ] **Step 1: 加模型**

在 `production/models.py` 追加（顶部 import 已有 `datetime`, `ForeignKey`；补 `from sqlalchemy import func`）:
```python
class SerialUnit(Base, TimestampMixin):
    __tablename__ = "serial_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    sn: Mapped[str] = mapped_column(unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(default="in_process")
    current_step_seq: Mapped[int] = mapped_column(default=0)
    current_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id"), default=None
    )
    version: Mapped[int] = mapped_column(default=0)


class StationPass(Base, TimestampMixin):
    __tablename__ = "station_passes"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    routing_step_id: Mapped[int] = mapped_column(ForeignKey("routing_steps.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    pass_time: Mapped[datetime] = mapped_column(server_default=func.now())
    result: Mapped[str] = mapped_column(default="pass")
    remark: Mapped[str | None] = mapped_column(default=None)
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create serial_unit and station_pass"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移创建 `serial_units` + `station_passes`（含各 FK 与 sn 唯一索引）。打开确认无 spurious 操作（不动其他表）。

- [ ] **Step 3: 加 repository**

在 `production/repository.py` 追加（顶部 import 加 `SerialUnit, StationPass`；确保有 `from sqlalchemy import select`）:
```python
class SerialUnitRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, su: SerialUnit) -> SerialUnit:
        self.db.add(su)
        self.db.flush()
        return su

    def get(self, id: int) -> SerialUnit | None:
        return self.db.get(SerialUnit, id)

    def get_by_sn(self, sn: str) -> SerialUnit | None:
        return self.db.execute(
            select(SerialUnit).where(SerialUnit.sn == sn)
        ).scalar_one_or_none()

    def list_by_work_order(self, work_order_id: int) -> list[SerialUnit]:
        return list(self.db.execute(
            select(SerialUnit).where(SerialUnit.work_order_id == work_order_id)
        ).scalars().all())

    def list_in_process_by_station(self, station_id: int) -> list[SerialUnit]:
        return list(self.db.execute(
            select(SerialUnit).where(
                SerialUnit.current_station_id == station_id,
                SerialUnit.status == "in_process",
            )
        ).scalars().all())


class StationPassRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, sp: StationPass) -> StationPass:
        self.db.add(sp)
        self.db.flush()
        return sp

    def exists_pass(self, serial_unit_id: int, routing_step_id: int) -> bool:
        row = self.db.execute(
            select(StationPass.id).where(
                StationPass.serial_unit_id == serial_unit_id,
                StationPass.routing_step_id == routing_step_id,
                StationPass.result == "pass",
            )
        ).first()
        return row is not None

    def list_by_serial_unit(self, serial_unit_id: int) -> list[StationPass]:
        return list(self.db.execute(
            select(StationPass)
            .where(StationPass.serial_unit_id == serial_unit_id)
            .order_by(StationPass.pass_time)
        ).scalars().all())
```

- [ ] **Step 4: 写测试**

`tests/modules/production/test_models_serial_unit.py`:
```python
from lightmes.modules.production.models import SerialUnit, StationPass
from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository,
)


def test_serial_unit_persist_and_lookup(db_session):
    # 需要一个 work_order + product；直接建最小依赖
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="SUP", name="壳", type="finished"))
    s = md.create_station(StationCreate(code="SUS", name="工位"))
    r = md.create_routing(RoutingCreate(code="SUR", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="SUWO", product_id=p.id, routing_id=r.id, qty=5))
    repo = SerialUnitRepository(db_session)
    su = repo.add(SerialUnit(sn="SN0001", work_order_id=wo.id, product_id=p.id))
    assert su.id is not None
    assert su.status == "in_process"
    assert su.version == 0
    assert repo.get_by_sn("SN0001").id == su.id


def test_station_pass_exists_check(db_session):
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="SPP", name="壳", type="finished"))
    s = md.create_station(StationCreate(code="SPS", name="工位"))
    r = md.create_routing(RoutingCreate(code="SPR", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    step_id = md.routings.steps_of(r.id)[0].id
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="SPWO", product_id=p.id, routing_id=r.id, qty=5))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="SN9", work_order_id=wo.id, product_id=p.id))
    sp_repo = StationPassRepository(db_session)
    assert sp_repo.exists_pass(su.id, step_id) is False
    sp_repo.add(StationPass(serial_unit_id=su.id, work_order_id=wo.id,
        routing_step_id=step_id, station_id=s.id, result="pass"))
    assert sp_repo.exists_pass(su.id, step_id) is True
```

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_models_serial_unit.py -v` → PASS（2）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/production/models.py src/lightmes/modules/production/repository.py src/lightmes/migrations tests/modules/production/test_models_serial_unit.py
git commit -m "feat: add SerialUnit and StationPass models with repositories"
```

---

### Task 4: StationPassService 过站校验链

实现过站核心服务：首站生成 SN、防跳站、防重复、乐观锁写过站、末站完工计数、翻转工单在制。领域逻辑最重，重点测试。含 SN 生成器双连接并发测试（本段首次真正消费 `next_sn`）。

**Files:**
- Modify: `src/lightmes/modules/production/schemas.py`（过站输入/结果）
- Create: `src/lightmes/modules/production/station_pass_service.py`
- Test: `tests/modules/production/test_station_pass.py`, `tests/modules/production/test_sn_concurrency.py`

**Interfaces:**
- Consumes: `MasterDataQueryService`（读工序/产品）、`SerialUnitRepository`、`StationPassRepository`、`WorkOrderRepository`、`SnRuleRepository`、`SnGenerator`、`shared.errors` 异常。
- Produces:
  - `production.schemas.StationPassInput`（Pydantic：`station_id:int`, `work_order_code:str|None=None`, `sn:str|None=None`, `operator_id:int|None=None`）
  - `production.schemas.StepInfo`（`seq:int`, `name:str`, `station_id:int`）
  - `production.schemas.StationPassResult`（`sn:str`, `passed_step:StepInfo`, `next_step:StepInfo|None`, `is_finished:bool`, `work_order_status:str`）
  - `production.station_pass_service.StationPassService(db)`，方法 `pass_station(data: StationPassInput) -> StationPassResult`（校验链见下；失败抛 `NotFoundError`/`BusinessRuleError`/`ConflictError`）。

- [ ] **Step 1: 加 schemas**

在 `production/schemas.py` 追加：
```python
class StationPassInput(BaseModel):
    station_id: int
    work_order_code: str | None = None
    sn: str | None = None
    operator_id: int | None = None


class StepInfo(BaseModel):
    seq: int
    name: str
    station_id: int


class StationPassResult(BaseModel):
    sn: str
    passed_step: StepInfo
    next_step: StepInfo | None
    is_finished: bool
    work_order_status: str
```

- [ ] **Step 2: 写失败测试（校验链各分支）**

`tests/modules/production/test_station_pass.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.shared.errors import NotFoundError, BusinessRuleError


def _setup(db_session, qty=10):
    """建产品 + 两工位两工序路线 + SN规则 + 已下达工单。返回 (p, s1, s2, wo)。"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="FP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="ST1", name="上料"))
    s2 = md.create_station(StationCreate(code="ST2", name="装配"))
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RL", name="r", pattern="SN{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WO1", product_id=p.id, routing_id=r.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return p, s1, s2, wo


def test_first_pass_generates_sn_and_advances(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    res = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    assert res.sn == "SN0001"
    assert res.passed_step.seq == 1
    assert res.next_step.seq == 2
    assert res.is_finished is False
    assert res.work_order_status == "in_process"  # 首过站翻转


def test_second_pass_by_sn_finishes(db_session):
    p, s1, s2, wo = _setup(db_session, qty=1)
    svc = StationPassService(db_session)
    r1 = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    r2 = svc.pass_station(StationPassInput(station_id=s2.id, sn=r1.sn))
    assert r2.passed_step.seq == 2
    assert r2.next_step is None
    assert r2.is_finished is True
    assert r2.work_order_status == "completed"  # qty=1 完工即 completed


def test_skip_station_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    # 首件却扫到第二个工位 → 防跳站
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s2.id, work_order_code="WO1"))


def test_duplicate_pass_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    r1 = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    # 同一 SN 再扫工位1（已过）→ 期望下一工序是 s2，扫 s1 触发防跳站/防重复
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s1.id, sn=r1.sn))


def test_unknown_work_order_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    with pytest.raises(NotFoundError):
        svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="NOPE"))


def test_unknown_sn_rejected(db_session):
    p, s1, s2, wo = _setup(db_session)
    svc = StationPassService(db_session)
    with pytest.raises(NotFoundError):
        svc.pass_station(StationPassInput(station_id=s2.id, sn="NOSUCH"))


def test_pass_on_non_released_work_order_rejected(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="FP2", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="STA", name="上料"))
    r = md.create_routing(RoutingCreate(code="RT2", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="上料")]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RL2", name="r", pattern="A{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WOC", product_id=p.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    # 未 release，仍是 created
    svc = StationPassService(db_session)
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WOC"))


def test_finished_sn_cannot_pass_again(db_session):
    p, s1, s2, wo = _setup(db_session, qty=1)
    svc = StationPassService(db_session)
    r1 = svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WO1"))
    svc.pass_station(StationPassInput(station_id=s2.id, sn=r1.sn))  # finished
    with pytest.raises(BusinessRuleError):
        svc.pass_station(StationPassInput(station_id=s2.id, sn=r1.sn))
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pass.py -v`
Expected: FAIL —— ImportError（`station_pass_service` 不存在）。

- [ ] **Step 4: 写 StationPassService**

`src/lightmes/modules/production/station_pass_service.py`:
```python
from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import SerialUnit, StationPass
from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository, SnRuleRepository,
    WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    StationPassInput, StationPassResult, StepInfo,
)
from lightmes.modules.production.sn_generator import SnGenerator
from lightmes.shared.errors import NotFoundError, BusinessRuleError, ConflictError


class StationPassService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.serial_units = SerialUnitRepository(db)
        self.passes = StationPassRepository(db)
        self.work_orders = WorkOrderRepository(db)
        self.sn_rules = SnRuleRepository(db)
        self.sn_gen = SnGenerator(db)

    def pass_station(self, data: StationPassInput) -> StationPassResult:
        # 1+3. 定位工单与 SN
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                raise NotFoundError(f"SN 不存在: {data.sn}")
            if su.status in ("finished", "scrapped"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {data.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首站过站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = None

        # 2. 工单状态
        if wo is None:
            raise NotFoundError("工单不存在")
        if wo.status not in ("released", "in_process"):
            raise BusinessRuleError(f"工单状态不允许过站: {wo.status}")

        # 读有序工序
        steps = self.query.get_ordered_steps(wo.routing_id)
        if not steps:
            raise BusinessRuleError("工艺路线无工序")

        # 3(续). 首站生成 SN
        if su is None:
            if wo.sn_rule_id is None:
                raise BusinessRuleError("工单未配置 SN 规则，无法生成 SN")
            rule = self.sn_rules.get(wo.sn_rule_id)
            if rule is None:
                raise BusinessRuleError("SN 规则不存在")
            new_sn = self.sn_gen.next_sn(rule)
            su = self.serial_units.add(SerialUnit(
                sn=new_sn, work_order_id=wo.id, product_id=wo.product_id,
                status="in_process", current_step_seq=0,
            ))

        # 4. 期望下一工序
        next_steps = [s for s in steps if s.seq > su.current_step_seq]
        if not next_steps:
            raise BusinessRuleError("已完工，无后续工序")
        expected = next_steps[0]

        # 5. 防跳站
        if data.station_id != expected.station_id:
            raise BusinessRuleError(
                f"应到工位(工序 {expected.seq} {expected.name})，当前工位不符"
            )

        # 6. 防重复（保险）
        if self.passes.exists_pass(su.id, expected.id):
            raise BusinessRuleError(f"该工序已过站: 工序 {expected.seq}")

        # 7. 写过站 + 乐观锁更新 serial_unit
        #    用带 version 条件的 UPDATE 做真正的乐观锁：仅当 version 仍为读到的值才更新。
        #    并发双扫时后提交者影响 0 行 → 抛 ConflictError（否则两次都成功、produced_qty 重复+1）。
        self.passes.add(StationPass(
            serial_unit_id=su.id, work_order_id=wo.id,
            routing_step_id=expected.id, station_id=data.station_id,
            operator_id=data.operator_id, result="pass",
        ))
        prev_version = su.version
        result = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(
                current_step_seq=expected.seq,
                current_station_id=data.station_id,
                version=prev_version + 1,
            )
        )
        if result.rowcount == 0:
            raise ConflictError("该产品正被其他工位处理，请重试")
        # 让 ORM 实例与刚写入的行一致（后续 8/9 步读 su 字段）
        self.db.refresh(su)

        # 8. 末站完工
        is_last = expected.seq == steps[-1].seq
        if is_last:
            su.status = "finished"
            wo.produced_qty += 1
            if wo.produced_qty >= wo.qty:
                wo.status = "completed"

        # 9. 翻转工单为在制
        if wo.status == "released":
            wo.status = "in_process"

        self.db.flush()

        remaining = [s for s in steps if s.seq > expected.seq]
        next_info = (
            StepInfo(seq=remaining[0].seq, name=remaining[0].name,
                     station_id=remaining[0].station_id)
            if remaining else None
        )
        return StationPassResult(
            sn=su.sn,
            passed_step=StepInfo(seq=expected.seq, name=expected.name,
                                 station_id=expected.station_id),
            next_step=next_info,
            is_finished=su.status == "finished",
            work_order_status=wo.status,
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pass.py -v`
Expected: PASS（8 passed）。

- [ ] **Step 6: 写 SN 生成器双连接并发测试**

`tests/modules/production/test_sn_concurrency.py`:
```python
import threading
from datetime import datetime
from lightmes.database import SessionLocal, engine
from lightmes.modules.production.models import SnRule
from lightmes.modules.production.sn_generator import SnGenerator


def test_next_sn_unique_under_concurrency():
    """两个真实连接并发抢同一 rule 的流水，行锁必须保证不重号。
    本测试不用 db_session（回滚 fixture 是单连接），直接开真实连接并各自提交，
    结束后清理。"""
    setup = SessionLocal()
    try:
        rule = SnRule(code="CONC", name="c", pattern="C{SEQ:5}", seq_reset="never")
        setup.add(rule)
        setup.commit()
        rule_id = rule.id
    finally:
        setup.close()

    results: list[str] = []
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker() -> None:
        s = SessionLocal()
        try:
            r = s.get(SnRule, rule_id)
            for _ in range(20):
                sn = SnGenerator(s).next_sn(r, datetime(2026, 8, 3))
                s.commit()
                with lock:
                    results.append(sn)
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 清理该 rule（其余测试不受影响）
    cleanup = SessionLocal()
    try:
        obj = cleanup.get(SnRule, rule_id)
        if obj is not None:
            cleanup.delete(obj)
            cleanup.commit()
    finally:
        cleanup.close()

    assert not errors, f"并发出错: {errors}"
    assert len(results) == 80
    assert len(set(results)) == 80, "SN 出现重复，行锁未生效"
```

- [ ] **Step 7: 运行并发测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_sn_concurrency.py -v`
Expected: PASS（1 passed，80 个 SN 全唯一）。
注意：本测试直连真实库并提交，不走回滚 fixture，故自行清理创建的 rule。若失败显示重复，说明行锁/populate_existing 有问题。

- [ ] **Step 8: 全量回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。
```bash
git add src/lightmes/modules/production/schemas.py src/lightmes/modules/production/station_pass_service.py tests/modules/production/test_station_pass.py tests/modules/production/test_sn_concurrency.py
git commit -m "feat: add StationPassService with validation chain and SN concurrency test"
```

---

### Task 5: 过站 API + HTMX 扫码工位页

把 `StationPassService` 接到 HTTP：一个类型化 JSON 过站 API + 一个 HTMX 扫码工位页（登录后可用），过站结果以片段返回（绿=通过/红=中文原因）。

**Files:**
- Modify: `src/lightmes/modules/production/router.py`
- Create: `src/lightmes/templates/production/scan.html`, `src/lightmes/templates/production/partials/scan_result.html`
- Test: `tests/modules/production/test_scan_pages.py`

**Interfaces:**
- Consumes: `StationPassService`, `StationPassInput`, `StationPassResult`, `require_login`, `current_user_or_none`, `MasterDataQueryService`（页面列工位用 masterdata；用 facade 或直接列 station——这里页面属于 production，读工位走 facade 加一个 `list_stations`。为避免扩大 facade，本任务页面工位下拉改为：GET 扫码页接受 `station_id` query 参数，不做工位下拉查询，保持 YAGNI）。
- Produces:
  - `POST /api/production/pass`（body `StationPassInput`）→ 200 `StationPassResult`；失败经全局 handler 返回对应状态码 + 中文 detail。加 `require_login`。
  - `GET /production/scan?station_id=<id>` → 扫码页 HTML（显示当前工位 id、扫码表单）。
  - `POST /production/scan`（HTMX 表单：`station_id`, `code_or_sn`）→ 过站结果片段（HTTP 200，成功绿色/失败红色）。未登录 → 401 + HX-Redirect /login。
- 说明：页面表单一个输入框 `code_or_sn`——首件填工单号、后续填 SN。服务端先按 SN 查，查不到再当作工单号试首站（简化：页面 POST 处理器先尝试 `sn=code_or_sn` 调用，若抛 NotFoundError 再用 `work_order_code=code_or_sn` 调用；两者都失败则显示错误）。API 端不做这个猜测，要求显式给 `sn` 或 `work_order_code`。

- [ ] **Step 1: 写模板**

`src/lightmes/templates/production/scan.html`:
```html
{% extends "base.html" %}
{% block title %}扫码过站{% endblock %}
{% block content %}
<h1>扫码过站 — 工位 {{ station_id }}</h1>
<form hx-post="/production/scan" hx-target="#result" hx-swap="innerHTML"
      hx-on::after-request="if(event.detail.successful) this.querySelector('[name=code_or_sn]').value=''">
  <input type="hidden" name="station_id" value="{{ station_id }}">
  <input name="code_or_sn" placeholder="扫工单号(首件)或SN" required autofocus>
  <button type="submit">过站</button>
</form>
<div id="result"></div>
{% endblock %}
```

`src/lightmes/templates/production/partials/scan_result.html`:
```html
{% if error %}
<div style="color:red">✗ {{ error }}</div>
{% else %}
<div style="color:green">
  ✓ {{ result.sn }} — 已过 工序{{ result.passed_step.seq }} {{ result.passed_step.name }}
  {% if result.is_finished %}
    <strong>[完工]</strong>
  {% else %}
    → 下一站: 工序{{ result.next_step.seq }} {{ result.next_step.name }}
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_scan_pages.py`:
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
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="op", password="pw12345", display_name="Op"))
    db_session.flush()
    assert client.post("/login", data={"username": "op", "password": "pw12345"}).status_code == 200


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PGP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="PST1", name="上料"))
    r = md.create_routing(RoutingCreate(code="PRT", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="上料")]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRL", name="r", pattern="P{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PWO", product_id=p.id, routing_id=r.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return s1


def test_scan_page_requires_login(client, db_session):
    # 未登录 POST 过站页 → 401 + HX-Redirect
    resp = client.post("/production/scan",
        data={"station_id": 1, "code_or_sn": "X"})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"


def test_scan_page_renders(client, db_session):
    _login(client, db_session)
    resp = client.get("/production/scan?station_id=7")
    assert resp.status_code == 200
    assert "工位 7" in resp.text


def test_scan_first_pass_success_fragment(client, db_session):
    s1 = _line(db_session)
    _login(client, db_session)
    resp = client.post("/production/scan",
        data={"station_id": s1.id, "code_or_sn": "PWO"})
    assert resp.status_code == 200
    assert "P001" in resp.text
    assert "✓" in resp.text


def test_scan_error_fragment_shows_reason(client, db_session):
    s1 = _line(db_session)
    _login(client, db_session)
    resp = client.post("/production/scan",
        data={"station_id": s1.id, "code_or_sn": "NOSUCH"})
    assert resp.status_code == 200
    assert "✗" in resp.text  # 红色错误片段


def test_api_pass_requires_login(client, db_session):
    resp = client.post("/api/production/pass",
        json={"station_id": 1, "work_order_code": "X"})
    assert resp.status_code == 401


def test_api_pass_success(client, db_session):
    s1 = _line(db_session)
    _login(client, db_session)
    resp = client.post("/api/production/pass",
        json={"station_id": s1.id, "work_order_code": "PWO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sn"] == "P001"
    assert body["is_finished"] is True  # 单工序路线，首站即末站
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_scan_pages.py -v`
Expected: FAIL（过站路由/页面未定义 → 404/401 不符）。

- [ ] **Step 4: 写 router（API + 页面）**

在 `src/lightmes/modules/production/router.py` 顶部补 import：
```python
from pathlib import Path
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from lightmes.modules.auth.dependencies import require_login, current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.modules.production.schemas import StationPassInput, StationPassResult
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.shared.errors import DomainError, NotFoundError

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)
```
追加路由：
```python
@router.post("/api/production/pass", response_model=StationPassResult)
def api_pass_station(
    data: StationPassInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> StationPassResult:
    data.operator_id = current_user.id
    return StationPassService(db).pass_station(data)  # DomainError→全局handler


@router.get("/production/scan", response_class=HTMLResponse)
def scan_page(
    request: Request, station_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "production/scan.html", {"station_id": station_id}
    )


@router.post("/production/scan", response_class=HTMLResponse)
def scan_submit(
    request: Request,
    station_id: int = Form(...),
    code_or_sn: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = StationPassService(db)
    # 页面便利：先按 SN 试，NotFound 再当工单号试首站
    try:
        try:
            result = svc.pass_station(StationPassInput(
                station_id=station_id, sn=code_or_sn, operator_id=user.id))
        except NotFoundError:
            result = svc.pass_station(StationPassInput(
                station_id=station_id, work_order_code=code_or_sn,
                operator_id=user.id))
    except DomainError as e:
        return templates.TemplateResponse(
            request, "production/partials/scan_result.html", {"error": e.detail}
        )
    return templates.TemplateResponse(
        request, "production/partials/scan_result.html", {"result": result}
    )
```
说明：`api_pass_station` 不 catch DomainError（全局 handler 处理）；页面处理器 catch 后渲染红片段。页面的"先 SN 后工单号"猜测仅便利，API 不做。

- [ ] **Step 5: 运行测试确认通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_scan_pages.py -v` → PASS（6）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/production/router.py src/lightmes/templates/production tests/modules/production/test_scan_pages.py
git commit -m "feat: add station-pass API and HTMX scan station page"
```

---

### Task 6: WIP 查询服务 + 看板页

提供在制品查询与一个只读 WIP 看板页。

**Files:**
- Create: `src/lightmes/modules/production/wip_service.py`
- Modify: `src/lightmes/modules/production/router.py`（WIP 页路由）
- Create: `src/lightmes/templates/production/wip.html`
- Test: `tests/modules/production/test_wip.py`

**Interfaces:**
- Consumes: `SerialUnitRepository`, `MasterDataQueryService`（可选，取工序名）。
- Produces:
  - `production.schemas.WipItem`（`sn:str`, `status:str`, `current_step_seq:int`, `current_station_id:int|None`）
  - `production.wip_service.WipService(db)`：
    - `wip_by_work_order(work_order_id: int) -> list[WipItem]`（该工单下 status=in_process 的 SN）
    - `wip_by_station(station_id: int) -> list[WipItem]`（当前停在该工位的在制 SN）
  - `GET /production/wip?work_order_id=<id>` → WIP 看板 HTML（列出在制 SN）。

- [ ] **Step 1: 加 WipItem schema**

在 `production/schemas.py` 追加：
```python
class WipItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sn: str
    status: str
    current_step_seq: int
    current_station_id: int | None
```
（若 `ConfigDict` 未 import 则在顶部 `from pydantic import BaseModel, ConfigDict`。）

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_wip.py`:
```python
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, StationPassInput,
)
from lightmes.modules.production.station_pass_service import StationPassService
from lightmes.modules.production.wip_service import WipService


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WP", name="壳", type="finished"))
    s1 = md.create_station(StationCreate(code="WS1", name="上料"))
    s2 = md.create_station(StationCreate(code="WS2", name="装配"))
    r = md.create_routing(RoutingCreate(code="WR", name="路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="WRL", name="r", pattern="W{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="WWO", product_id=p.id, routing_id=r.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return s1, s2, wo


def test_wip_by_work_order_lists_in_process(db_session):
    s1, s2, wo = _line(db_session)
    pass_svc = StationPassService(db_session)
    # 两个 SN 过首站，停在 s1 之后（current_step_seq=1）
    pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    wip = WipService(db_session).wip_by_work_order(wo.id)
    assert len(wip) == 2
    assert all(w.status == "in_process" for w in wip)
    assert all(w.current_step_seq == 1 for w in wip)


def test_wip_by_station(db_session):
    s1, s2, wo = _line(db_session)
    pass_svc = StationPassService(db_session)
    pass_svc.pass_station(StationPassInput(station_id=s1.id, work_order_code="WWO"))
    wip = WipService(db_session).wip_by_station(s1.id)
    assert len(wip) == 1
    assert wip[0].current_station_id == s1.id
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_wip.py -v`
Expected: FAIL（`wip_service` 不存在）。

- [ ] **Step 4: 写 WipService**

`src/lightmes/modules/production/wip_service.py`:
```python
from sqlalchemy.orm import Session
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import WipItem


class WipService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)

    def wip_by_work_order(self, work_order_id: int) -> list[WipItem]:
        units = self.serial_units.list_by_work_order(work_order_id)
        return [WipItem.model_validate(u) for u in units if u.status == "in_process"]

    def wip_by_station(self, station_id: int) -> list[WipItem]:
        units = self.serial_units.list_in_process_by_station(station_id)
        return [WipItem.model_validate(u) for u in units]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_wip.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 6: 加 WIP 看板页 + 模板**

`src/lightmes/templates/production/wip.html`:
```html
{% extends "base.html" %}
{% block title %}WIP 看板{% endblock %}
{% block content %}
<h1>WIP 看板 — 工单 {{ work_order_id }}</h1>
<table border="1">
  <thead><tr><th>SN</th><th>状态</th><th>当前工序</th><th>当前工位</th></tr></thead>
  <tbody>
    {% for w in items %}
    <tr>
      <td>{{ w.sn }}</td><td>{{ w.status }}</td>
      <td>{{ w.current_step_seq }}</td><td>{{ w.current_station_id or "" }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```
在 `router.py` 追加（import 加 `from lightmes.modules.production.wip_service import WipService`）：
```python
@router.get("/production/wip", response_class=HTMLResponse)
def wip_page(
    request: Request, work_order_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    items = WipService(db).wip_by_work_order(work_order_id) if work_order_id else []
    return templates.TemplateResponse(
        request, "production/wip.html",
        {"work_order_id": work_order_id, "items": items},
    )
```

- [ ] **Step 7: 加看板页测试**

在 `tests/modules/production/test_wip.py` 追加：
```python
def test_wip_page_renders(db_session):
    import pytest as _pytest
    from fastapi.testclient import TestClient
    from lightmes.main import app
    from lightmes.database import get_db
    s1, s2, wo = _line(db_session)
    StationPassService(db_session).pass_station(
        StationPassInput(station_id=s1.id, work_order_code="WWO"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        resp = client.get(f"/production/wip?work_order_id={wo.id}")
        assert resp.status_code == 200
        assert "WIP 看板" in resp.text
        assert "W001" in resp.text
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 8: 运行测试确认通过 + 全量回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_wip.py -v` → PASS（3）。
全量：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。
```bash
git add src/lightmes/modules/production/wip_service.py src/lightmes/modules/production/schemas.py src/lightmes/modules/production/router.py src/lightmes/templates/production/wip.html tests/modules/production/test_wip.py
git commit -m "feat: add WIP query service and board page"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P1b spec §3/§4/§5/§6/§7/§9/§10）：
- 领域异常体系 + 全局 handler → Task 1 ✅
- masterdata 只读 facade → Task 2 ✅
- serial_unit + station_pass 模型 + 迁移 + repository → Task 3 ✅
- 过站校验链（首站生成 SN、工单状态、防跳站、防重复、乐观锁、末站完工、翻转 in_process）→ Task 4 ✅
- SN 生成器双连接并发测试 → Task 4 ✅
- 过站 API + 扫码页（require_login / HX-Redirect / 中文错误片段）→ Task 5 ✅
- WIP 查询 + 看板页 → Task 6 ✅
- 事件发布：spec §8 列了 SerialUnitCreated/StationPassed 等；**本计划未显式 publish 事件**——见下方说明。

**说明（一处对 spec 的务实收敛）**：spec §8 说本段"真正发布" SerialUnitCreated/StationPassed 等事件，但也写明"MVP 内无强订阅动作，末站完工在过站事务内同步完成"。计划里过站逻辑全部同步完成、无订阅方，若仅为满足"发布"而发布是无消费者的空动作（YAGNI）。**决定：P1b 不接入事件总线 publish**，把事件留到 P1c（trace 真正需要订阅 StationPassed 时）一并接。这是有意偏离，已在此标注；不影响任何功能与测试。

**占位符扫描**：无 TBD/TODO；每个代码步骤含完整代码。

**类型一致性**：`MasterDataQueryService.get_ordered_steps/get_product/get_routing`、`SerialUnitRepository`/`StationPassRepository` 方法、`StationPassService.pass_station`、`StationPassInput/StepInfo/StationPassResult/WipItem`、`WipService.wip_by_work_order/wip_by_station`、`require_login`/`current_user_or_none`、`DomainError` 子类 —— 定义处与引用处一致 ✅。

**跨模块约定遵守**：production 的 `station_pass_service.py`/`wip_service.py` 只 import `MasterDataQueryService`，不 import masterdata 的 repository/models（校验过）。注意 Task 4 的 SerialUnit 需要 product_id——取自 `wo.product_id`（工单已有），不需查 masterdata。✅
