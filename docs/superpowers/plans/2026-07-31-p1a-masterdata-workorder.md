# P1a 主数据 + 工单 + SN 生成器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P0 骨架上实现 P1 的第一段：主数据（产品/工位/工艺路线/BOM）、SN 编码规则与可配置 SN 生成器、工单，配套最简 HTMX 管理页，使得能配置一条产线并创建工单。

**Architecture:** 沿用 P0 模块化单体约定。新增两个模块 `masterdata`（product/station/routing/routing_step/bom/bom_item）与 `production`（sn_rule/work_order + SN 生成器）。每模块 `models/schemas/repository/service/router/__init__.py`，`__init__.register(app)` 挂路由，`main.py` 调 `register`，`migrations/env.py` 导入 models 以纳入 autogenerate。领域逻辑真实 DB 集成测试（`db_session` 回滚 fixture），SN 生成器纯函数部分单测。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管）, PostgreSQL+TimescaleDB, pytest, uv。

## Global Constraints

- Python 3.12；依赖用 `uv`（`uv run` 跑命令）。
- SQLAlchemy 2.0 风格：`Mapped[]` + `mapped_column()`，继承 `lightmes.shared.base.Base` + `TimestampMixin`。
- 所有 schema 变更走 Alembic autogenerate；新模型必须在 `src/lightmes/migrations/env.py` 导入以注册到 `Base.metadata`。
- 模块边界：对外只暴露 service 层；跨模块不直接引用他模块 models/repository。
- 每模块 `__init__.py` 提供 `register(app: FastAPI) -> None`，内部 `from ... import router` 后 `app.include_router(router)`；`main.py` 调 `<module>.register(app)`。
- API 端点用 `response_model=` 类型化（喂 OpenAPI）；入参用 Pydantic schema 校验；失败用 `HTTPException` + 中文 detail。
- 事务边界在 `get_db`（请求级 commit/rollback）；repository 只 `flush()`，不 commit。
- 集成测试连真实 PostgreSQL（`db_session` fixture）。测试命令必须用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v`
- 迁移/建表后需 `alembic upgrade head`（同样用 127.0.0.1 的 DATABASE_URL 前缀）。
- HTMX 前端服务端渲染，模板 `{{ }}` 自动转义；第三方 JS 用 P0 已本地托管的 `/static/vendor/htmx.min.js`；无 SPA。
- 提交前缀 `feat:`/`chore:`/`test:`/`refactor:`；每个 Task 末尾提交。DRY / YAGNI / TDD。
- Shell 用 bash 语法（正斜杠路径，`/dev/null`）。DB 需 running（`docker compose up -d db`）。

---

## File Structure

P1a 结束时新增/修改（仅列 P1a 相关）：

```
src/lightmes/modules/masterdata/
├── __init__.py            # register(app)
├── models.py              # Product, Station, Routing, RoutingStep, Bom, BomItem
├── schemas.py             # *Create / *Read Pydantic 模型
├── repository.py          # 各实体数据访问
├── service.py             # MasterDataService (业务规则: 唯一active路线/BOM等)
└── router.py              # /api/masterdata/* 类型化API + /masterdata/* HTMX页面
src/lightmes/modules/production/
├── __init__.py            # register(app)
├── models.py              # SnRule, WorkOrder
├── schemas.py
├── repository.py
├── service.py             # WorkOrderService (create/release/status)
├── sn_generator.py        # 纯函数 validate_pattern/period_key/render + SnGenerator(db)
└── router.py              # /api/production/* + /production/* 页面
src/lightmes/templates/masterdata/   # 各实体 list+form 片段/页面
src/lightmes/templates/production/   # sn_rule / work_order 页面
src/lightmes/templates/home.html     # 简单导航首页
src/lightmes/migrations/versions/    # 各任务的 autogenerate 迁移
tests/modules/masterdata/            # 各实体测试
tests/modules/production/            # sn_generator / work_order 测试
```

修改：`src/lightmes/main.py`（注册两模块 + home 路由）、`src/lightmes/migrations/env.py`（导入新 models）、`tests/conftest.py`（清理 P0 遗留未用 import）。

---

### Task 1: masterdata 模块脚手架 + Product 模型 + 迁移

建立 `masterdata` 模块骨架，落地第一个实体 `Product` 及其 repository/service/schema/CRUD API，生成迁移，注册进 app。顺带清理 P0 遗留的 conftest 未用 import。

**Files:**
- Create: `src/lightmes/modules/masterdata/__init__.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `router.py`
- Modify: `src/lightmes/main.py`（注册 masterdata）、`src/lightmes/migrations/env.py`（导入 masterdata.models）、`tests/conftest.py`（删未用 import）
- Create: `src/lightmes/migrations/versions/<auto>_create_product.py`（autogenerate）
- Test: `tests/modules/masterdata/__init__.py`, `tests/modules/masterdata/test_product.py`

**Interfaces:**
- Consumes: `lightmes.shared.base.Base`/`TimestampMixin`, `lightmes.database.get_db`, FastAPI。
- Produces:
  - `masterdata.models.Product`（表 `products`）：`id:int PK`, `code:str unique index`, `name:str`, `type:str`（finished/semi/component/consumable）, `spec:str|None`, `unit:str`, `track_mode:str`（serial/batch/none, default "none"）, + timestamps。
  - `masterdata.schemas.ProductCreate`（code/name/type/spec?|None/unit/track_mode default "none"）、`ProductRead`（id + 上述字段, `ConfigDict(from_attributes=True)`）。
  - `masterdata.repository.ProductRepository(db)`：`add(product)->Product`, `get(id)->Product|None`, `get_by_code(code)->Product|None`, `list_all()->list[Product]`。
  - `masterdata.service.MasterDataService(db)`：`create_product(data: ProductCreate)->Product`（code 重复则 `ValueError`）。
  - `masterdata.register(app)`。
  - API：`POST /api/masterdata/products`（body ProductCreate → 201 ProductRead；code 冲突 → 409）、`GET /api/masterdata/products`（→ list[ProductRead]）。

- [ ] **Step 1: 清理 conftest 未用 import**

`tests/conftest.py` 头部改为（删除 `import os` 和 `from sqlalchemy import text`）：
```python
import pytest
from lightmes.database import SessionLocal, engine
```
（其余 fixture 不变。）

- [ ] **Step 2: 写 Product 模型**

`src/lightmes/modules/masterdata/models.py`:
```python
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    type: Mapped[str] = mapped_column()  # finished/semi/component/consumable
    spec: Mapped[str | None] = mapped_column(default=None)
    unit: Mapped[str] = mapped_column(default="pcs")
    track_mode: Mapped[str] = mapped_column(default="none")  # serial/batch/none
```

`src/lightmes/modules/masterdata/__init__.py`:
```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from lightmes.modules.masterdata.router import router

    app.include_router(router)
```

- [ ] **Step 3: 导入 model 到 alembic env 并生成迁移**

在 `src/lightmes/migrations/env.py` 的 auth import 下方加一行：
```python
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
```
生成并应用迁移：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create product"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 生成的迁移仅 `op.create_table("products", ...)`（含 code 唯一索引）；upgrade 成功。打开迁移确认无 spurious 操作（不得动 users/其他表）。

- [ ] **Step 4: 写 repository + schemas + service**

`src/lightmes/modules/masterdata/repository.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product


class ProductRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, product: Product) -> Product:
        self.db.add(product)
        self.db.flush()
        return product

    def get(self, id: int) -> Product | None:
        return self.db.get(Product, id)

    def get_by_code(self, code: str) -> Product | None:
        return self.db.execute(
            select(Product).where(Product.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[Product]:
        return list(self.db.execute(select(Product)).scalars().all())
```

`src/lightmes/modules/masterdata/schemas.py`:
```python
from pydantic import BaseModel, ConfigDict


class ProductCreate(BaseModel):
    code: str
    name: str
    type: str
    unit: str = "pcs"
    track_mode: str = "none"
    spec: str | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    type: str
    unit: str
    track_mode: str
    spec: str | None
```

`src/lightmes/modules/masterdata/service.py`:
```python
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product
from lightmes.modules.masterdata.repository import ProductRepository
from lightmes.modules.masterdata.schemas import ProductCreate


class MasterDataService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)

    def create_product(self, data: ProductCreate) -> Product:
        if self.products.get_by_code(data.code) is not None:
            raise ValueError(f"产品编码已存在: {data.code}")
        product = Product(
            code=data.code,
            name=data.name,
            type=data.type,
            unit=data.unit,
            track_mode=data.track_mode,
            spec=data.spec,
        )
        return self.products.add(product)
```

- [ ] **Step 5: 写失败测试**

`tests/modules/masterdata/__init__.py`: 空文件。

`tests/modules/masterdata/test_product.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate


def test_create_product_persists(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(
        ProductCreate(code="NBK-A", name="外壳A", type="finished", unit="pcs")
    )
    assert p.id is not None
    assert p.code == "NBK-A"
    assert p.track_mode == "none"


def test_create_product_duplicate_code_rejected(db_session):
    svc = MasterDataService(db_session)
    svc.create_product(ProductCreate(code="DUP", name="x", type="component"))
    with pytest.raises(ValueError):
        svc.create_product(ProductCreate(code="DUP", name="y", type="component"))
```

- [ ] **Step 6: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_product.py -v`
Expected: FAIL —— ImportError（service 未建）或表不存在。（若在 Step 4 之后运行，应为 GREEN；本步用于确认 TDD 红。）

- [ ] **Step 7: 写 router 并注册**

`src/lightmes/modules/masterdata/router.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.masterdata.schemas import ProductCreate, ProductRead
from lightmes.modules.masterdata.service import MasterDataService

router = APIRouter()


@router.post(
    "/api/masterdata/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    data: ProductCreate, db: Session = Depends(get_db)
) -> ProductRead:
    try:
        product = MasterDataService(db).create_product(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ProductRead.model_validate(product)


@router.get("/api/masterdata/products", response_model=list[ProductRead])
def list_products(db: Session = Depends(get_db)) -> list[ProductRead]:
    products = MasterDataService(db).products.list_all()
    return [ProductRead.model_validate(p) for p in products]
```

在 `src/lightmes/main.py` 注册：import 处加 `from lightmes.modules import masterdata`，在 `auth.register(app)` 下方加 `masterdata.register(app)`。

- [ ] **Step 8: 运行测试确认通过 + API 冒烟**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_product.py -v`
Expected: PASS（2 passed）。
再跑全量确认无回归：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/masterdata src/lightmes/main.py src/lightmes/migrations tests/modules/masterdata tests/conftest.py
git commit -m "feat: add masterdata module with Product entity and CRUD API"
```

---

### Task 2: Station 实体 + 迁移 + CRUD API

在 masterdata 模块加 `Station`，扩展 repository/service/schema/API。

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`, `schemas.py`, `repository.py`, `service.py`, `router.py`
- Create: `src/lightmes/migrations/versions/<auto>_create_station.py`
- Test: `tests/modules/masterdata/test_station.py`

**Interfaces:**
- Consumes: 现有 masterdata 模块结构。
- Produces:
  - `models.Station`（表 `stations`）：`id:int PK`, `code:str unique index`, `name:str`, `description:str|None`, `location:str|None`, `is_active:bool default True`, + timestamps。
  - `schemas.StationCreate`（code/name/description?|None/location?|None）、`StationRead`（id + 字段 + is_active）。
  - `repository.StationRepository(db)`：`add`, `get(id)`, `get_by_code(code)`, `list_all()`。
  - `service.MasterDataService.create_station(data: StationCreate)->Station`（code 重复 → `ValueError`）。
  - API：`POST /api/masterdata/stations`（201 StationRead；冲突 409）、`GET /api/masterdata/stations`（list）。

- [ ] **Step 1: 加 Station 模型**

在 `models.py` 追加：
```python
class Station(Base, TimestampMixin):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    location: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create station"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移仅创建 `stations` 表（含 code 唯一索引）。确认无 spurious 操作。

- [ ] **Step 3: 加 schemas**

在 `schemas.py` 追加：
```python
class StationCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    location: str | None = None


class StationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None
    location: str | None
    is_active: bool
```

- [ ] **Step 4: 加 repository**

在 `repository.py` 追加（顶部 import 加 `Station`）：
```python
class StationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, station: Station) -> Station:
        self.db.add(station)
        self.db.flush()
        return station

    def get(self, id: int) -> Station | None:
        return self.db.get(Station, id)

    def get_by_code(self, code: str) -> Station | None:
        return self.db.execute(
            select(Station).where(Station.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[Station]:
        return list(self.db.execute(select(Station)).scalars().all())
```

- [ ] **Step 5: 写失败测试**

`tests/modules/masterdata/test_station.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import StationCreate


def test_create_station_persists(db_session):
    svc = MasterDataService(db_session)
    s = svc.create_station(StationCreate(code="ST-01", name="装配1"))
    assert s.id is not None
    assert s.is_active is True


def test_create_station_duplicate_code_rejected(db_session):
    svc = MasterDataService(db_session)
    svc.create_station(StationCreate(code="ST-DUP", name="x"))
    with pytest.raises(ValueError):
        svc.create_station(StationCreate(code="ST-DUP", name="y"))
```

- [ ] **Step 6: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_station.py -v`
Expected: FAIL（`create_station` 未定义）。

- [ ] **Step 7: 加 service 方法 + API**

在 `service.py` 的 `MasterDataService.__init__` 加 `self.stations = StationRepository(db)`（顶部 import 加 `Station`, `StationRepository`, `StationCreate`），并加方法：
```python
    def create_station(self, data: StationCreate) -> Station:
        if self.stations.get_by_code(data.code) is not None:
            raise ValueError(f"工位编码已存在: {data.code}")
        station = Station(
            code=data.code,
            name=data.name,
            description=data.description,
            location=data.location,
        )
        return self.stations.add(station)
```

在 `router.py` 追加（import 加 `StationCreate, StationRead`）：
```python
@router.post(
    "/api/masterdata/stations",
    response_model=StationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_station(
    data: StationCreate, db: Session = Depends(get_db)
) -> StationRead:
    try:
        station = MasterDataService(db).create_station(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return StationRead.model_validate(station)


@router.get("/api/masterdata/stations", response_model=list[StationRead])
def list_stations(db: Session = Depends(get_db)) -> list[StationRead]:
    stations = MasterDataService(db).stations.list_all()
    return [StationRead.model_validate(s) for s in stations]
```

- [ ] **Step 8: 运行测试确认通过 + 回归**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_station.py -v` → PASS（2）。
全量：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/masterdata src/lightmes/migrations tests/modules/masterdata
git commit -m "feat: add Station entity and CRUD API to masterdata"
```

---

### Task 3: Routing + RoutingStep 实体 + 迁移 + API

加工艺路线头 `Routing` 与工序 `RoutingStep`，含"同一产品同时只一条 active 路线"规则与工序顺序唯一约束。

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`, `schemas.py`, `repository.py`, `service.py`, `router.py`
- Create: `src/lightmes/migrations/versions/<auto>_create_routing.py`
- Test: `tests/modules/masterdata/test_routing.py`

**Interfaces:**
- Consumes: 现有 masterdata；`Product`, `Station`。
- Produces:
  - `models.Routing`（表 `routings`）：`id:int PK`, `code:str unique index`, `name:str`, `product_id:int FK products.id`, `version:str default "1"`, `status:str default "active"`（active/inactive）, + timestamps。
  - `models.RoutingStep`（表 `routing_steps`）：`id:int PK`, `routing_id:int FK routings.id`, `seq:int`, `station_id:int FK stations.id`, `name:str`, `is_mandatory:bool default True`, `binding_config` 预留（`Mapped[dict|None]` 用 `JSON`，default None）, + timestamps。唯一约束 `(routing_id, seq)`。
  - `schemas.RoutingStepCreate`（seq/station_id/name/is_mandatory default True）、`RoutingCreate`（code/name/product_id/version default "1"/steps: list[RoutingStepCreate]）、`RoutingStepRead`、`RoutingRead`（含 steps 列表, 按 seq 排序）。
  - `repository.RoutingRepository(db)`：`add(routing)->Routing`, `get(id)->Routing|None`, `get_active_by_product(product_id)->Routing|None`, `list_all()->list[Routing]`。
  - `service.MasterDataService.create_routing(data: RoutingCreate)->Routing`：校验 code 唯一、product 存在、每个 step 的 station 存在、seq 无重复；若该 product 已有 active 路线则新建的置 `status="inactive"`（保证同时只一条 active），否则 active。
  - API：`POST /api/masterdata/routings`（201 RoutingRead；校验失败 400/409）、`GET /api/masterdata/routings`（list）、`GET /api/masterdata/routings/{id}`（单个含 steps；404）。

- [ ] **Step 1: 加 Routing / RoutingStep 模型**

在 `models.py` 追加（顶部 import 加 `from sqlalchemy import ForeignKey, JSON, UniqueConstraint`）：
```python
class Routing(Base, TimestampMixin):
    __tablename__ = "routings"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    version: Mapped[str] = mapped_column(default="1")
    status: Mapped[str] = mapped_column(default="active")  # active/inactive


class RoutingStep(Base, TimestampMixin):
    __tablename__ = "routing_steps"
    __table_args__ = (UniqueConstraint("routing_id", "seq", name="uq_routing_step_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    seq: Mapped[int] = mapped_column()
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    name: Mapped[str] = mapped_column()
    is_mandatory: Mapped[bool] = mapped_column(default=True)
    binding_config: Mapped[dict | None] = mapped_column(JSON, default=None)
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create routing"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移创建 `routings` + `routing_steps`（含 FK 与 `uq_routing_step_seq` 唯一约束）。确认无 spurious 操作。

- [ ] **Step 3: 加 schemas**

在 `schemas.py` 追加：
```python
class RoutingStepCreate(BaseModel):
    seq: int
    station_id: int
    name: str
    is_mandatory: bool = True


class RoutingCreate(BaseModel):
    code: str
    name: str
    product_id: int
    version: str = "1"
    steps: list[RoutingStepCreate]


class RoutingStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    seq: int
    station_id: int
    name: str
    is_mandatory: bool


class RoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    product_id: int
    version: str
    status: str
    steps: list[RoutingStepRead]
```

- [ ] **Step 4: 加 repository**

在 `repository.py` 追加（import 加 `Routing, RoutingStep`）：
```python
class RoutingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, routing: Routing) -> Routing:
        self.db.add(routing)
        self.db.flush()
        return routing

    def get(self, id: int) -> Routing | None:
        return self.db.get(Routing, id)

    def get_by_code(self, code: str) -> Routing | None:
        return self.db.execute(
            select(Routing).where(Routing.code == code)
        ).scalar_one_or_none()

    def get_active_by_product(self, product_id: int) -> Routing | None:
        return self.db.execute(
            select(Routing).where(
                Routing.product_id == product_id, Routing.status == "active"
            )
        ).scalar_one_or_none()

    def list_all(self) -> list[Routing]:
        return list(self.db.execute(select(Routing)).scalars().all())

    def steps_of(self, routing_id: int) -> list[RoutingStep]:
        return list(
            self.db.execute(
                select(RoutingStep)
                .where(RoutingStep.routing_id == routing_id)
                .order_by(RoutingStep.seq)
            ).scalars().all()
        )
```

- [ ] **Step 5: 写失败测试**

`tests/modules/masterdata/test_routing.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)


def _setup_product_and_stations(svc):
    p = svc.create_product(ProductCreate(code="P1", name="壳", type="finished"))
    s1 = svc.create_station(StationCreate(code="S1", name="工位1"))
    s2 = svc.create_station(StationCreate(code="S2", name="工位2"))
    return p, s1, s2


def test_create_routing_with_steps(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    r = svc.create_routing(RoutingCreate(
        code="R1", name="主路线", product_id=p.id,
        steps=[
            RoutingStepCreate(seq=1, station_id=s1.id, name="上料"),
            RoutingStepCreate(seq=2, station_id=s2.id, name="装配"),
        ],
    ))
    assert r.id is not None
    assert r.status == "active"
    steps = svc.routings.steps_of(r.id)
    assert [s.seq for s in steps] == [1, 2]


def test_second_routing_for_same_product_is_inactive(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    svc.create_routing(RoutingCreate(code="R1", name="v1", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="a")]))
    r2 = svc.create_routing(RoutingCreate(code="R2", name="v2", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s1.id, name="a")]))
    assert r2.status == "inactive"


def test_duplicate_seq_rejected(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="R9", name="x", product_id=p.id,
            steps=[
                RoutingStepCreate(seq=1, station_id=s1.id, name="a"),
                RoutingStepCreate(seq=1, station_id=s2.id, name="b"),
            ]))


def test_unknown_station_rejected(db_session):
    svc = MasterDataService(db_session)
    p, s1, s2 = _setup_product_and_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="R8", name="x", product_id=p.id,
            steps=[RoutingStepCreate(seq=1, station_id=99999, name="a")]))
```

- [ ] **Step 6: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_routing.py -v`
Expected: FAIL（`create_routing` 未定义）。

- [ ] **Step 7: 加 service 方法 + API**

在 `service.py`（import 加 `Routing, RoutingStep, RoutingRepository, RoutingCreate`），`__init__` 加 `self.routings = RoutingRepository(db)`，并加方法：
```python
    def create_routing(self, data: RoutingCreate) -> Routing:
        if self.routings.get_by_code(data.code) is not None:
            raise ValueError(f"路线编码已存在: {data.code}")
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        seqs = [s.seq for s in data.steps]
        if len(seqs) != len(set(seqs)):
            raise ValueError("工序 seq 不能重复")
        for step in data.steps:
            if self.stations.get(step.station_id) is None:
                raise ValueError(f"工位不存在: {step.station_id}")
        has_active = self.routings.get_active_by_product(data.product_id) is not None
        routing = Routing(
            code=data.code,
            name=data.name,
            product_id=data.product_id,
            version=data.version,
            status="inactive" if has_active else "active",
        )
        self.routings.add(routing)
        for step in data.steps:
            self.db.add(RoutingStep(
                routing_id=routing.id,
                seq=step.seq,
                station_id=step.station_id,
                name=step.name,
                is_mandatory=step.is_mandatory,
            ))
        self.db.flush()
        return routing
```

在 `router.py`（import 加 `RoutingCreate, RoutingRead, RoutingStepRead`）追加：
```python
@router.post(
    "/api/masterdata/routings",
    response_model=RoutingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_routing(
    data: RoutingCreate, db: Session = Depends(get_db)
) -> RoutingRead:
    svc = MasterDataService(db)
    try:
        routing = svc.create_routing(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    steps = svc.routings.steps_of(routing.id)
    return RoutingRead(
        id=routing.id, code=routing.code, name=routing.name,
        product_id=routing.product_id, version=routing.version,
        status=routing.status,
        steps=[RoutingStepRead.model_validate(s) for s in steps],
    )


@router.get("/api/masterdata/routings/{routing_id}", response_model=RoutingRead)
def get_routing(routing_id: int, db: Session = Depends(get_db)) -> RoutingRead:
    svc = MasterDataService(db)
    routing = svc.routings.get(routing_id)
    if routing is None:
        raise HTTPException(status_code=404, detail="路线不存在")
    steps = svc.routings.steps_of(routing.id)
    return RoutingRead(
        id=routing.id, code=routing.code, name=routing.name,
        product_id=routing.product_id, version=routing.version,
        status=routing.status,
        steps=[RoutingStepRead.model_validate(s) for s in steps],
    )
```

- [ ] **Step 8: 运行测试确认通过 + 回归**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_routing.py -v` → PASS（4）。
全量回归 → 全绿。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/masterdata src/lightmes/migrations tests/modules/masterdata
git commit -m "feat: add Routing and RoutingStep with active-routing rule"
```

---

### Task 4: Bom + BomItem 实体 + 迁移 + API

加物料清单头 `Bom` 与行 `BomItem`，含"同一产品同时只一条 active BOM"规则；`bom_item.track_mode` 冗余自组件 product（供 P1c 谱系校验用）。

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`, `schemas.py`, `repository.py`, `service.py`, `router.py`
- Create: `src/lightmes/migrations/versions/<auto>_create_bom.py`
- Test: `tests/modules/masterdata/test_bom.py`

**Interfaces:**
- Consumes: 现有 masterdata；`Product`。
- Produces:
  - `models.Bom`（表 `boms`）：`id:int PK`, `product_id:int FK products.id`, `version:str default "1"`, `status:str default "active"`, + timestamps。
  - `models.BomItem`（表 `bom_items`）：`id:int PK`, `bom_id:int FK boms.id`, `component_product_id:int FK products.id`, `qty:Numeric default 1`, `track_mode:str`, + timestamps。唯一约束 `(bom_id, component_product_id)`。
  - `schemas.BomItemCreate`（component_product_id/qty default 1）、`BomCreate`（product_id/version default "1"/items: list[BomItemCreate]）、`BomItemRead`（含 track_mode）、`BomRead`（含 items）。
  - `repository.BomRepository(db)`：`add`, `get(id)`, `get_active_by_product(product_id)`, `list_all()`, `items_of(bom_id)`。
  - `service.MasterDataService.create_bom(data: BomCreate)->Bom`：校验 product 存在、每个 component_product 存在、item 无重复组件；每行 `track_mode` 取组件 product 的 track_mode；product 已有 active BOM 则新建置 inactive。
  - API：`POST /api/masterdata/boms`（201 BomRead）、`GET /api/masterdata/boms/{id}`（含 items；404）。

- [ ] **Step 1: 加 Bom / BomItem 模型**

在 `models.py` 追加（顶部 import 补 `Numeric`）：
```python
class Bom(Base, TimestampMixin):
    __tablename__ = "boms"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    version: Mapped[str] = mapped_column(default="1")
    status: Mapped[str] = mapped_column(default="active")


class BomItem(Base, TimestampMixin):
    __tablename__ = "bom_items"
    __table_args__ = (
        UniqueConstraint("bom_id", "component_product_id", name="uq_bom_item_component"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    track_mode: Mapped[str] = mapped_column()
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create bom"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移创建 `boms` + `bom_items`（含 FK 与 `uq_bom_item_component`）。确认无 spurious 操作。

- [ ] **Step 3: 加 schemas**

在 `schemas.py` 追加：
```python
class BomItemCreate(BaseModel):
    component_product_id: int
    qty: float = 1


class BomCreate(BaseModel):
    product_id: int
    version: str = "1"
    items: list[BomItemCreate]


class BomItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    component_product_id: int
    qty: float
    track_mode: str


class BomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    version: str
    status: str
    items: list[BomItemRead]
```

- [ ] **Step 4: 加 repository**

在 `repository.py` 追加（import 加 `Bom, BomItem`）：
```python
class BomRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, bom: Bom) -> Bom:
        self.db.add(bom)
        self.db.flush()
        return bom

    def get(self, id: int) -> Bom | None:
        return self.db.get(Bom, id)

    def get_active_by_product(self, product_id: int) -> Bom | None:
        return self.db.execute(
            select(Bom).where(Bom.product_id == product_id, Bom.status == "active")
        ).scalar_one_or_none()

    def list_all(self) -> list[Bom]:
        return list(self.db.execute(select(Bom)).scalars().all())

    def items_of(self, bom_id: int) -> list[BomItem]:
        return list(
            self.db.execute(
                select(BomItem).where(BomItem.bom_id == bom_id)
            ).scalars().all()
        )
```

- [ ] **Step 5: 写失败测试**

`tests/modules/masterdata/test_bom.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, BomCreate, BomItemCreate,
)


def _finished_and_components(svc):
    fin = svc.create_product(ProductCreate(code="F1", name="成品", type="finished"))
    c_ser = svc.create_product(
        ProductCreate(code="C-SER", name="主板", type="component", track_mode="serial")
    )
    c_bat = svc.create_product(
        ProductCreate(code="C-BAT", name="螺丝", type="consumable", track_mode="batch")
    )
    return fin, c_ser, c_bat


def test_create_bom_copies_component_track_mode(db_session):
    svc = MasterDataService(db_session)
    fin, c_ser, c_bat = _finished_and_components(svc)
    bom = svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id, qty=1),
        BomItemCreate(component_product_id=c_bat.id, qty=4),
    ]))
    items = {i.component_product_id: i for i in svc.boms.items_of(bom.id)}
    assert items[c_ser.id].track_mode == "serial"
    assert items[c_bat.id].track_mode == "batch"
    assert bom.status == "active"


def test_second_bom_for_same_product_inactive(db_session):
    svc = MasterDataService(db_session)
    fin, c_ser, _ = _finished_and_components(svc)
    svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id)]))
    bom2 = svc.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id)]))
    assert bom2.status == "inactive"


def test_unknown_component_rejected(db_session):
    svc = MasterDataService(db_session)
    fin, _, _ = _finished_and_components(svc)
    with pytest.raises(ValueError):
        svc.create_bom(BomCreate(product_id=fin.id, items=[
            BomItemCreate(component_product_id=99999)]))
```

- [ ] **Step 6: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_bom.py -v`
Expected: FAIL（`create_bom` 未定义）。

- [ ] **Step 7: 加 service 方法 + API**

在 `service.py`（import 加 `Bom, BomItem, BomRepository, BomCreate`），`__init__` 加 `self.boms = BomRepository(db)`，加方法：
```python
    def create_bom(self, data: BomCreate) -> Bom:
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        comp_ids = [i.component_product_id for i in data.items]
        if len(comp_ids) != len(set(comp_ids)):
            raise ValueError("BOM 行组件不能重复")
        components = {}
        for item in data.items:
            comp = self.products.get(item.component_product_id)
            if comp is None:
                raise ValueError(f"组件不存在: {item.component_product_id}")
            components[item.component_product_id] = comp
        has_active = self.boms.get_active_by_product(data.product_id) is not None
        bom = Bom(
            product_id=data.product_id,
            version=data.version,
            status="inactive" if has_active else "active",
        )
        self.boms.add(bom)
        for item in data.items:
            self.db.add(BomItem(
                bom_id=bom.id,
                component_product_id=item.component_product_id,
                qty=item.qty,
                track_mode=components[item.component_product_id].track_mode,
            ))
        self.db.flush()
        return bom
```

在 `router.py`（import 加 `BomCreate, BomRead, BomItemRead`）追加：
```python
@router.post(
    "/api/masterdata/boms",
    response_model=BomRead,
    status_code=status.HTTP_201_CREATED,
)
def create_bom(data: BomCreate, db: Session = Depends(get_db)) -> BomRead:
    svc = MasterDataService(db)
    try:
        bom = svc.create_bom(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    items = svc.boms.items_of(bom.id)
    return BomRead(
        id=bom.id, product_id=bom.product_id, version=bom.version,
        status=bom.status,
        items=[BomItemRead.model_validate(i) for i in items],
    )


@router.get("/api/masterdata/boms/{bom_id}", response_model=BomRead)
def get_bom(bom_id: int, db: Session = Depends(get_db)) -> BomRead:
    svc = MasterDataService(db)
    bom = svc.boms.get(bom_id)
    if bom is None:
        raise HTTPException(status_code=404, detail="BOM 不存在")
    items = svc.boms.items_of(bom.id)
    return BomRead(
        id=bom.id, product_id=bom.product_id, version=bom.version,
        status=bom.status,
        items=[BomItemRead.model_validate(i) for i in items],
    )
```

- [ ] **Step 8: 运行测试确认通过 + 回归**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_bom.py -v` → PASS（3）。
全量回归 → 全绿。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/masterdata src/lightmes/migrations tests/modules/masterdata
git commit -m "feat: add Bom and BomItem with active-BOM rule and denormalized track_mode"
```

---

### Task 5: production 模块 + SnRule 模型 + 可配置 SN 生成器

建立 `production` 模块骨架，落地 `SnRule` 模型与 SN 生成器（纯函数部分：pattern 校验、周期键、渲染；有状态部分：`SnGenerator(db)` 读改写流水并加行锁）。

**Files:**
- Create: `src/lightmes/modules/production/__init__.py`, `models.py`, `schemas.py`, `repository.py`, `sn_generator.py`, `router.py`
- Modify: `src/lightmes/main.py`（注册 production）、`src/lightmes/migrations/env.py`（导入 production.models）
- Create: `src/lightmes/migrations/versions/<auto>_create_sn_rule.py`
- Test: `tests/modules/production/__init__.py`, `tests/modules/production/test_sn_generator.py`

**Interfaces:**
- Consumes: `Base`/`TimestampMixin`, `get_db`。
- Produces:
  - `production.models.SnRule`（表 `sn_rules`）：`id:int PK`, `code:str unique index`, `name:str`, `product_id:int|None FK products.id`, `pattern:str`, `seq_reset:str default "never"`（never/daily/monthly）, `current_seq:int default 0`, `seq_period_key:str|None default None`, + timestamps。
  - `sn_generator.validate_pattern(pattern: str) -> None`：非法（未知占位符 / `{SEQ}` 缺位数 / `{SEQ:x}` 非数字）抛 `ValueError`；合法返回 None。合法占位符：`{YYYY}{YY}{MM}{DD}{SEQ:n}`；其余字面输出。
  - `sn_generator.period_key(seq_reset: str, now: datetime) -> str`：never→`"*"`；daily→`"%Y-%m-%d"`；monthly→`"%Y-%m"`。
  - `sn_generator.render(pattern: str, seq: int, now: datetime) -> str`：替换日期占位符与 `{SEQ:n}`（n 位补零）。
  - `sn_generator.SnGenerator(db)`：`next_sn(rule: SnRule, now: datetime | None = None) -> str`——对该 rule 行 `SELECT ... FOR UPDATE`，按 period_key 判断重置或自增 `current_seq`，更新 `seq_period_key`，render 出 SN 返回。使用 `datetime.now()` 若未传 now。
  - `production.repository.SnRuleRepository(db)`：`add`, `get(id)`, `get_by_code(code)`。
  - `production.register(app)`。

- [ ] **Step 1: production 模块骨架 + SnRule 模型**

`src/lightmes/modules/production/__init__.py`:
```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from lightmes.modules.production.router import router

    app.include_router(router)
```

`src/lightmes/modules/production/models.py`:
```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class SnRule(Base, TimestampMixin):
    __tablename__ = "sn_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), default=None
    )
    pattern: Mapped[str] = mapped_column()
    seq_reset: Mapped[str] = mapped_column(default="never")  # never/daily/monthly
    current_seq: Mapped[int] = mapped_column(default=0)
    seq_period_key: Mapped[str | None] = mapped_column(default=None)
```

- [ ] **Step 2: 导入 model 到 alembic env 并迁移**

在 `src/lightmes/migrations/env.py` 追加：
```python
from lightmes.modules.production import models as _production_models  # noqa: F401
```
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create sn_rule"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移仅创建 `sn_rules`。无 spurious。

- [ ] **Step 3: 写失败测试（纯函数 + 生成器）**

`tests/modules/production/__init__.py`: 空文件。

`tests/modules/production/test_sn_generator.py`:
```python
from datetime import datetime
import pytest
from lightmes.modules.production.sn_generator import (
    validate_pattern, period_key, render, SnGenerator,
)
from lightmes.modules.production.models import SnRule


def test_validate_pattern_accepts_valid():
    validate_pattern("SN{YY}{MM}{DD}{SEQ:5}")  # no raise


def test_validate_pattern_rejects_unknown_placeholder():
    with pytest.raises(ValueError):
        validate_pattern("SN{FOO}{SEQ:3}")


def test_validate_pattern_rejects_seq_without_width():
    with pytest.raises(ValueError):
        validate_pattern("SN{SEQ}")


def test_period_key():
    now = datetime(2026, 7, 31, 10, 0, 0)
    assert period_key("never", now) == "*"
    assert period_key("daily", now) == "2026-07-31"
    assert period_key("monthly", now) == "2026-07"


def test_render_pads_seq_and_date():
    now = datetime(2026, 7, 5, 0, 0, 0)
    assert render("SN{YY}{MM}{DD}{SEQ:4}", 42, now) == "SN2607050042"


def test_next_sn_increments(db_session):
    rule = SnRule(code="R", name="r", pattern="A{SEQ:3}", seq_reset="never")
    db_session.add(rule)
    db_session.flush()
    gen = SnGenerator(db_session)
    now = datetime(2026, 7, 31)
    assert gen.next_sn(rule, now) == "A001"
    assert gen.next_sn(rule, now) == "A002"


def test_next_sn_resets_on_new_period(db_session):
    rule = SnRule(code="R2", name="r", pattern="{SEQ:2}", seq_reset="daily")
    db_session.add(rule)
    db_session.flush()
    gen = SnGenerator(db_session)
    assert gen.next_sn(rule, datetime(2026, 7, 31)) == "01"
    assert gen.next_sn(rule, datetime(2026, 7, 31)) == "02"
    assert gen.next_sn(rule, datetime(2026, 8, 1)) == "01"  # reset new day
```

- [ ] **Step 4: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_sn_generator.py -v`
Expected: FAIL（`sn_generator` 未建）。

- [ ] **Step 5: 写 sn_generator 实现**

`src/lightmes/modules/production/sn_generator.py`:
```python
import re
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import SnRule

# 匹配 {TOKEN} 或 {TOKEN:arg}，arg 允许任意非空白以便校验非法宽度
_TOKEN = re.compile(r"\{([A-Za-z]+)(?::([^}]*))?\}")
_KNOWN_DATE = {"YYYY", "YY", "MM", "DD"}


def validate_pattern(pattern: str) -> None:
    for m in _TOKEN.finditer(pattern):
        name, width = m.group(1), m.group(2)
        if name == "SEQ":
            if width is None or not width.isdigit():
                raise ValueError("占位符 {SEQ} 必须带数字位数, 如 {SEQ:5}")
        elif name not in _KNOWN_DATE:
            raise ValueError(f"未知占位符: {{{name}}}")


def period_key(seq_reset: str, now: datetime) -> str:
    if seq_reset == "never":
        return "*"
    if seq_reset == "daily":
        return now.strftime("%Y-%m-%d")
    if seq_reset == "monthly":
        return now.strftime("%Y-%m")
    raise ValueError(f"未知 seq_reset: {seq_reset}")


def render(pattern: str, seq: int, now: datetime) -> str:
    def repl(m: re.Match) -> str:
        name, width = m.group(1), m.group(2)
        if name == "YYYY":
            return now.strftime("%Y")
        if name == "YY":
            return now.strftime("%y")
        if name == "MM":
            return now.strftime("%m")
        if name == "DD":
            return now.strftime("%d")
        if name == "SEQ":
            return str(seq).zfill(int(width))
        return m.group(0)

    return _TOKEN.sub(repl, pattern)


class SnGenerator:
    def __init__(self, db: Session) -> None:
        self.db = db

    def next_sn(self, rule: SnRule, now: datetime | None = None) -> str:
        now = now or datetime.now()
        # 对该 rule 行加锁并强制从锁定行刷新属性（populate_existing），
        # 否则若该实例已在 session identity map 中，读到的是内存里的旧 current_seq，
        # 并发下两事务各自 +1 会产生重复 SN，使行锁形同虚设。
        locked = self.db.execute(
            select(SnRule)
            .where(SnRule.id == rule.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        current_key = period_key(locked.seq_reset, now)
        if locked.seq_period_key != current_key:
            locked.seq_period_key = current_key
            locked.current_seq = 1
        else:
            locked.current_seq += 1
        self.db.flush()
        return render(locked.pattern, locked.current_seq, now)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_sn_generator.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 7: 写 repository + 空 router 并注册**

`src/lightmes/modules/production/repository.py`:
```python
from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import SnRule


class SnRuleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, rule: SnRule) -> SnRule:
        self.db.add(rule)
        self.db.flush()
        return rule

    def get(self, id: int) -> SnRule | None:
        return self.db.get(SnRule, id)

    def get_by_code(self, code: str) -> SnRule | None:
        return self.db.execute(
            select(SnRule).where(SnRule.code == code)
        ).scalar_one_or_none()
```

`src/lightmes/modules/production/router.py`:
```python
from fastapi import APIRouter

router = APIRouter()
# SnRule + WorkOrder 端点在 Task 6 加入
```

在 `src/lightmes/main.py`：import 加 `from lightmes.modules import production`，`masterdata.register(app)` 下方加 `production.register(app)`。

- [ ] **Step 8: 全量回归**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v`
Expected: 全绿（含新增 7 个 sn_generator 测试）。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/production src/lightmes/main.py src/lightmes/migrations tests/modules/production
git commit -m "feat: add production module with SnRule and configurable SN generator"
```

---

### Task 6: WorkOrder 模型 + 服务（创建/下达）+ SnRule/WorkOrder API

加工单 `WorkOrder` 与其服务（创建、release 状态流转），以及 SnRule 与 WorkOrder 的类型化 API。

**Files:**
- Modify: `src/lightmes/modules/production/models.py`, `schemas.py`, `repository.py`, `router.py`, `service.py`(新建)
- Create: `src/lightmes/modules/production/service.py`
- Create: `src/lightmes/migrations/versions/<auto>_create_work_order.py`
- Test: `tests/modules/production/test_work_order.py`

**Interfaces:**
- Consumes: `SnRule`, `SnRuleRepository`, `validate_pattern`；masterdata 的 `Product`/`Routing`（跨模块只读校验：通过 id 存在性检查，用 `db.get` 直接查表，避免耦合 masterdata service）。
- Produces:
  - `production.models.WorkOrder`（表 `work_orders`）：`id:int PK`, `code:str unique index`, `product_id:int FK products.id`, `routing_id:int FK routings.id`, `sn_rule_id:int|None FK sn_rules.id`, `qty:int`, `status:str default "created"`（created/released/in_progress/completed/closed）, `source:str default "manual"`, `produced_qty:int default 0`, `planned_start:datetime|None`, `planned_end:datetime|None`, + timestamps。
  - `schemas.SnRuleCreate`（code/name/pattern/seq_reset default "never"/product_id?|None）、`SnRuleRead`。
  - `schemas.WorkOrderCreate`（code/product_id/routing_id/qty/sn_rule_id?|None）、`WorkOrderRead`（全字段）。
  - `production.service.ProductionService(db)`：
    - `create_sn_rule(data: SnRuleCreate)->SnRule`（先 `validate_pattern(data.pattern)`，code 重复 → ValueError）。
    - `create_work_order(data: WorkOrderCreate)->WorkOrder`（校验 code 唯一、product/routing 存在、sn_rule 若给定则存在；status="created"）。
    - `release_work_order(work_order_id: int)->WorkOrder`（仅 `created` → `released`；否则 ValueError）。
  - API：`POST /api/production/sn-rules`（201 SnRuleRead；pattern 非法 400；code 冲突 409）、`POST /api/production/work-orders`（201 WorkOrderRead；400/409）、`POST /api/production/work-orders/{id}/release`（200 WorkOrderRead；非法状态 409）、`GET /api/production/work-orders/{id}`（404）。

- [ ] **Step 1: 加 WorkOrder 模型**

在 `production/models.py` 追加（顶部 import 补 `from datetime import datetime`）：
```python
class WorkOrder(Base, TimestampMixin):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    sn_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("sn_rules.id"), default=None
    )
    qty: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")
    source: Mapped[str] = mapped_column(default="manual")
    produced_qty: Mapped[int] = mapped_column(default=0)
    planned_start: Mapped[datetime | None] = mapped_column(default=None)
    planned_end: Mapped[datetime | None] = mapped_column(default=None)
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create work_order"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移仅创建 `work_orders`（含 FK 到 products/routings/sn_rules）。无 spurious。

- [ ] **Step 3: 加 schemas**

在 `production/schemas.py`（新建或追加；若尚无该文件则创建含以下全部）：
```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SnRuleCreate(BaseModel):
    code: str
    name: str
    pattern: str
    seq_reset: str = "never"
    product_id: int | None = None


class SnRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    pattern: str
    seq_reset: str
    product_id: int | None


class WorkOrderCreate(BaseModel):
    code: str
    product_id: int
    routing_id: int
    qty: int
    sn_rule_id: int | None = None


class WorkOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    product_id: int
    routing_id: int
    sn_rule_id: int | None
    qty: int
    status: str
    source: str
    produced_qty: int
    planned_start: datetime | None
    planned_end: datetime | None
```

- [ ] **Step 4: 加 WorkOrder repository 方法**

在 `production/repository.py` 追加（import 加 `WorkOrder`）：
```python
class WorkOrderRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, wo: WorkOrder) -> WorkOrder:
        self.db.add(wo)
        self.db.flush()
        return wo

    def get(self, id: int) -> WorkOrder | None:
        return self.db.get(WorkOrder, id)

    def get_by_code(self, code: str) -> WorkOrder | None:
        return self.db.execute(
            select(WorkOrder).where(WorkOrder.code == code)
        ).scalar_one_or_none()
```

- [ ] **Step 5: 写失败测试**

`tests/modules/production/test_work_order.py`:
```python
import pytest
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, StationCreate, RoutingCreate, RoutingStepCreate,
)


def _line(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WP", name="壳", type="finished"))
    s = md.create_station(StationCreate(code="WS", name="工位"))
    r = md.create_routing(RoutingCreate(code="WR", name="路线", product_id=p.id,
        steps=[RoutingStepCreate(seq=1, station_id=s.id, name="装配")]))
    return p, r


def test_create_sn_rule_validates_pattern(db_session):
    svc = ProductionService(db_session)
    with pytest.raises(ValueError):
        svc.create_sn_rule(SnRuleCreate(code="BAD", name="x", pattern="{SEQ}"))


def test_create_and_release_work_order(db_session):
    p, r = _line(db_session)
    svc = ProductionService(db_session)
    wo = svc.create_work_order(WorkOrderCreate(
        code="WO-1", product_id=p.id, routing_id=r.id, qty=10))
    assert wo.status == "created"
    released = svc.release_work_order(wo.id)
    assert released.status == "released"


def test_release_non_created_rejected(db_session):
    p, r = _line(db_session)
    svc = ProductionService(db_session)
    wo = svc.create_work_order(WorkOrderCreate(
        code="WO-2", product_id=p.id, routing_id=r.id, qty=5))
    svc.release_work_order(wo.id)
    with pytest.raises(ValueError):
        svc.release_work_order(wo.id)  # already released


def test_create_work_order_unknown_product_rejected(db_session):
    p, r = _line(db_session)
    svc = ProductionService(db_session)
    with pytest.raises(ValueError):
        svc.create_work_order(WorkOrderCreate(
            code="WO-3", product_id=99999, routing_id=r.id, qty=1))
```

- [ ] **Step 6: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_work_order.py -v`
Expected: FAIL（`ProductionService` 未建）。

- [ ] **Step 7: 写 service + API**

`src/lightmes/modules/production/service.py`:
```python
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product, Routing
from lightmes.modules.production.models import SnRule, WorkOrder
from lightmes.modules.production.repository import (
    SnRuleRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.sn_generator import validate_pattern


class ProductionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sn_rules = SnRuleRepository(db)
        self.work_orders = WorkOrderRepository(db)

    def create_sn_rule(self, data: SnRuleCreate) -> SnRule:
        validate_pattern(data.pattern)  # 非法 pattern 抛 ValueError
        if self.sn_rules.get_by_code(data.code) is not None:
            raise ValueError(f"SN 规则编码已存在: {data.code}")
        rule = SnRule(
            code=data.code, name=data.name, pattern=data.pattern,
            seq_reset=data.seq_reset, product_id=data.product_id,
        )
        return self.sn_rules.add(rule)

    def create_work_order(self, data: WorkOrderCreate) -> WorkOrder:
        if self.work_orders.get_by_code(data.code) is not None:
            raise ValueError(f"工单号已存在: {data.code}")
        if self.db.get(Product, data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        if self.db.get(Routing, data.routing_id) is None:
            raise ValueError(f"路线不存在: {data.routing_id}")
        if data.sn_rule_id is not None and self.sn_rules.get(data.sn_rule_id) is None:
            raise ValueError(f"SN 规则不存在: {data.sn_rule_id}")
        wo = WorkOrder(
            code=data.code, product_id=data.product_id,
            routing_id=data.routing_id, sn_rule_id=data.sn_rule_id,
            qty=data.qty, status="created",
        )
        return self.work_orders.add(wo)

    def release_work_order(self, work_order_id: int) -> WorkOrder:
        wo = self.work_orders.get(work_order_id)
        if wo is None:
            raise ValueError(f"工单不存在: {work_order_id}")
        if wo.status != "created":
            raise ValueError(f"仅 created 状态可下达, 当前: {wo.status}")
        wo.status = "released"
        self.db.flush()
        return wo
```

`src/lightmes/modules/production/router.py`（覆盖 Task 5 的占位）:
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.production.schemas import (
    SnRuleCreate, SnRuleRead, WorkOrderCreate, WorkOrderRead,
)
from lightmes.modules.production.service import ProductionService

router = APIRouter()


@router.post(
    "/api/production/sn-rules",
    response_model=SnRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sn_rule(data: SnRuleCreate, db: Session = Depends(get_db)) -> SnRuleRead:
    svc = ProductionService(db)
    try:
        rule = svc.create_sn_rule(data)
    except ValueError as e:
        # pattern 非法与 code 冲突都走 ValueError；用 400 统一（code 冲突亦可接受）
        raise HTTPException(status_code=400, detail=str(e))
    return SnRuleRead.model_validate(rule)


@router.post(
    "/api/production/work-orders",
    response_model=WorkOrderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_work_order(
    data: WorkOrderCreate, db: Session = Depends(get_db)
) -> WorkOrderRead:
    svc = ProductionService(db)
    try:
        wo = svc.create_work_order(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WorkOrderRead.model_validate(wo)


@router.post(
    "/api/production/work-orders/{work_order_id}/release",
    response_model=WorkOrderRead,
)
def release_work_order(
    work_order_id: int, db: Session = Depends(get_db)
) -> WorkOrderRead:
    svc = ProductionService(db)
    try:
        wo = svc.release_work_order(work_order_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return WorkOrderRead.model_validate(wo)


@router.get(
    "/api/production/work-orders/{work_order_id}", response_model=WorkOrderRead
)
def get_work_order(
    work_order_id: int, db: Session = Depends(get_db)
) -> WorkOrderRead:
    wo = ProductionService(db).work_orders.get(work_order_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return WorkOrderRead.model_validate(wo)
```

- [ ] **Step 8: 运行测试确认通过 + 回归**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_work_order.py -v` → PASS（4）。
全量回归 → 全绿。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/production src/lightmes/migrations tests/modules/production
git commit -m "feat: add WorkOrder model, production service and sn-rule/work-order API"
```

---

### Task 7: 最简 HTMX 管理页（product/station + 首页导航）

给主数据加最简可用界面，先做 product 与 station 两个列表+新增页（验证 UI 竖切贯通），并加一个首页导航。routing/bom/sn_rule/work_order 的页面因结构相同，本任务只做 product+station 作为模式样板，其余留到 P1b 前按需补（YAGNI：先让核心配置能点）。

**Files:**
- Create: `src/lightmes/templates/home.html`
- Create: `src/lightmes/templates/masterdata/products.html`, `src/lightmes/templates/masterdata/partials/product_row.html`
- Create: `src/lightmes/templates/masterdata/stations.html`, `src/lightmes/templates/masterdata/partials/station_row.html`
- Modify: `src/lightmes/modules/masterdata/router.py`（加页面路由）、`src/lightmes/main.py`（加 `/` 首页）
- Test: `tests/modules/masterdata/test_pages.py`

**Interfaces:**
- Consumes: `MasterDataService`, `Jinja2Templates`（模式同 auth router：`Path(__file__).resolve().parent.parent.parent / "templates"`）。
- Produces:
  - `GET /` → 首页 HTML（导航到各管理页 + 登录）。
  - `GET /masterdata/products` → 产品列表页（表格 + 新增表单，HTMX）。
  - `POST /masterdata/products` → 新增产品，返回新行片段（`product_row.html`）追加到表格；code 冲突返回红色错误片段。
  - `GET /masterdata/stations` / `POST /masterdata/stations` → 同上。

- [ ] **Step 1: 写首页与模板**

`src/lightmes/templates/home.html`:
```html
{% extends "base.html" %}
{% block title %}LightMES{% endblock %}
{% block content %}
<h1>LightMES</h1>
<ul>
  <li><a href="/masterdata/products">产品管理</a></li>
  <li><a href="/masterdata/stations">工位管理</a></li>
  <li><a href="/login">登录</a></li>
</ul>
{% endblock %}
```

`src/lightmes/templates/masterdata/partials/product_row.html`:
```html
<tr>
  <td>{{ product.id }}</td>
  <td>{{ product.code }}</td>
  <td>{{ product.name }}</td>
  <td>{{ product.type }}</td>
  <td>{{ product.unit }}</td>
  <td>{{ product.track_mode }}</td>
</tr>
```

`src/lightmes/templates/masterdata/products.html`:
```html
{% extends "base.html" %}
{% block title %}产品管理{% endblock %}
{% block content %}
<h1>产品管理</h1>
<form hx-post="/masterdata/products" hx-target="#rows" hx-swap="beforeend"
      hx-on::after-request="if(event.detail.successful) this.reset()">
  <input name="code" placeholder="编码" required>
  <input name="name" placeholder="名称" required>
  <select name="type">
    <option value="finished">成品</option>
    <option value="semi">半成品</option>
    <option value="component">组件</option>
    <option value="consumable">辅料</option>
  </select>
  <input name="unit" placeholder="单位" value="pcs">
  <select name="track_mode">
    <option value="none">不追踪</option>
    <option value="serial">唯一件</option>
    <option value="batch">批次</option>
  </select>
  <button type="submit">新增</button>
</form>
<div id="msg"></div>
<table border="1">
  <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>类型</th><th>单位</th><th>追踪</th></tr></thead>
  <tbody id="rows">
    {% for product in products %}{% include "masterdata/partials/product_row.html" %}{% endfor %}
  </tbody>
</table>
{% endblock %}
```

`src/lightmes/templates/masterdata/partials/station_row.html`:
```html
<tr>
  <td>{{ station.id }}</td>
  <td>{{ station.code }}</td>
  <td>{{ station.name }}</td>
  <td>{{ station.location or "" }}</td>
</tr>
```

`src/lightmes/templates/masterdata/stations.html`:
```html
{% extends "base.html" %}
{% block title %}工位管理{% endblock %}
{% block content %}
<h1>工位管理</h1>
<form hx-post="/masterdata/stations" hx-target="#rows" hx-swap="beforeend"
      hx-on::after-request="if(event.detail.successful) this.reset()">
  <input name="code" placeholder="编码" required>
  <input name="name" placeholder="名称" required>
  <input name="location" placeholder="位置">
  <button type="submit">新增</button>
</form>
<table border="1">
  <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>位置</th></tr></thead>
  <tbody id="rows">
    {% for station in stations %}{% include "masterdata/partials/station_row.html" %}{% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 2: 写失败测试**

`tests/modules/masterdata/test_pages.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_home_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LightMES" in resp.text


def test_products_page_renders(client):
    resp = client.get("/masterdata/products")
    assert resp.status_code == 200
    assert "产品管理" in resp.text


def test_create_product_via_page_returns_row(client):
    resp = client.post(
        "/masterdata/products",
        data={"code": "UI-1", "name": "壳", "type": "finished",
              "unit": "pcs", "track_mode": "none"},
    )
    assert resp.status_code == 200
    assert "UI-1" in resp.text


def test_stations_page_renders(client):
    resp = client.get("/masterdata/stations")
    assert resp.status_code == 200
    assert "工位管理" in resp.text
```

- [ ] **Step 3: 运行测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_pages.py -v`
Expected: FAIL（页面路由未定义 → 404）。

- [ ] **Step 4: 加页面路由**

在 `src/lightmes/modules/masterdata/router.py` 顶部补 import：
```python
from pathlib import Path
from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)
```
追加页面路由：
```python
@router.get("/masterdata/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    products = MasterDataService(db).products.list_all()
    return templates.TemplateResponse(
        request, "masterdata/products.html", {"products": products}
    )


@router.post("/masterdata/products", response_class=HTMLResponse)
def products_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    unit: str = Form("pcs"),
    track_mode: str = Form("none"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from lightmes.modules.masterdata.schemas import ProductCreate
    svc = MasterDataService(db)
    try:
        product = svc.create_product(ProductCreate(
            code=code, name=name, type=type, unit=unit, track_mode=track_mode))
    except ValueError as e:
        return HTMLResponse(f'<tr><td colspan="6" style="color:red">{e}</td></tr>')
    return templates.TemplateResponse(
        request, "masterdata/partials/product_row.html", {"product": product}
    )


@router.get("/masterdata/stations", response_class=HTMLResponse)
def stations_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    stations = MasterDataService(db).stations.list_all()
    return templates.TemplateResponse(
        request, "masterdata/stations.html", {"stations": stations}
    )


@router.post("/masterdata/stations", response_class=HTMLResponse)
def stations_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    location: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from lightmes.modules.masterdata.schemas import StationCreate
    svc = MasterDataService(db)
    try:
        station = svc.create_station(StationCreate(
            code=code, name=name, location=location or None))
    except ValueError as e:
        return HTMLResponse(f'<tr><td colspan="4" style="color:red">{e}</td></tr>')
    return templates.TemplateResponse(
        request, "masterdata/partials/station_row.html", {"station": station}
    )
```

在 `src/lightmes/main.py` 加首页路由（在 `/health` 附近）:
```python
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "home.html")
```

- [ ] **Step 5: 运行测试确认通过 + 全量回归**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_pages.py -v` → PASS（4）。
全量：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v` → 全绿。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/templates src/lightmes/modules/masterdata/router.py src/lightmes/main.py tests/modules/masterdata/test_pages.py
git commit -m "feat: add minimal HTMX admin pages for product/station and home nav"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P1 spec §4/§5.1/§7 的 P1a 部分）：
- product/station/routing/routing_step/bom/bom_item 模型 + 迁移 + API → Task 1/2/3/4 ✅
- 同一产品同时只一条 active 路线/BOM 规则 → Task 3/4 service ✅
- bom_item.track_mode 冗余自组件 → Task 4 ✅
- routing_step.binding_config 预留 → Task 3 模型（不实现逻辑）✅
- sn_rule + 可配置 SN 生成器（占位符/流水/重置/行锁并发唯一）→ Task 5 ✅
- work_order + 状态流转 created→released → Task 6 ✅
- 最简管理页（HTMX）→ Task 7（product/station 样板；其余页面 YAGNI 留后）✅
- 集成测试真实 DB + SN 生成器测试 → 全程 ✅

**说明**：P1a 不含过站/serial_unit/station_pass（P1b），不含谱系（P1c）——与 spec §7 切分一致。routing/bom/sn_rule/work_order 的**管理页**只做了 product/station 样板，API 齐全；其余实体可经 API 配置，页面待 P1b 按现场需要补，符合 YAGNI。work_order 的 `in_progress/completed/closed` 转换由 P1b 过站触发，本段只做到 `released`。

**占位符扫描**：无 TBD/TODO；每个代码步骤含完整代码。

**类型一致性**：`MasterDataService.create_product/create_station/create_routing/create_bom` 与其 repository（products/stations/routings/boms）、schemas（*Create/*Read）签名一致；`ProductionService.create_sn_rule/create_work_order/release_work_order`、`SnGenerator.next_sn`、`validate_pattern/period_key/render`、跨模块只读用 `db.get(Product/Routing, id)` —— 定义处与引用处一致 ✅。

**跨模块边界**：production 校验 product/routing 存在时用 `db.get(masterdata.models.X, id)` 直接查表（只读存在性检查），不调 masterdata service，避免服务层耦合；符合"模块只暴露 service、跨模块轻量协作"的约定。

