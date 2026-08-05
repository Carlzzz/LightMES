# P2b 主数据管理 UI + ERP 同步抽象层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐主数据（产线/作业站/工艺路径/BOM/SN规则）的 HTMX 管理界面，并建立 ERP 主数据下行同步的抽象层（`ErpSyncService` 接口 + 文件导入实现），把真接金蝶隔离在将来填的 adapter 后。

**Architecture:** 先数据模型层（product/bom/routing 加 source/erp_ref/synced_at + erp_ref 部分唯一索引），再同步业务逻辑（masterdata 加 upsert_product/upsert_bom，按 erp_ref upsert、打 source=erp 标记、幂等、不覆盖 manual），再 integration 模块（ErpSyncService 抽象 + FileErpSyncService 解析 CSV/JSON），最后 UI（各实体管理页 + ERP 导入页 + 来源徽标）。沿用模块化单体全部约定：facade/service 跨模块边界、领域异常、真实 DB 集成测试、require_login 写守卫、HTMX 薄荷绿卡片、get_db 事务边界。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`（`uv run`）。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0：`Mapped[]`/`mapped_column()`，继承 `Base`+`TimestampMixin`。所有 schema 变更走 Alembic；autogenerate 后**打开迁移确认只动预期表**（元数据对齐纪律，防部分索引漂移；uq_active_*/uq_operation_* 等既有部分/唯一索引不得被误删）。
- **ERP 同步仅下行**：ERP 是权威源，MES 只读同步 + MES 特有字段本地维护，**不回写**。
- 受 ERP 管辖实体：`product`/`bom`/`routing` 加 `source`(default "manual")/`erp_ref`(str|None)/`synced_at`(datetime|None) + erp_ref 部分唯一索引。`line`/`work_station`/`operation`/`sn_rule`/`bom_item` 不加 source。
- **upsert 语义**：按 `erp_ref` 匹配；存在→更新 ERP 管字段 + synced_at，不动 MES 本地字段；不存在→新建 source="erp"。source="manual"（erp_ref 空）永不被覆盖。幂等：重复导入同 erp_ref 走 updated。
- **导入部分成功**：坏行/条记入 `SyncResult.errors` 跳过，好的照常导入，不整批回滚。
- 导入格式：product→CSV、bom→JSON（各单一格式；不做每实体双解析器）。本期 ERP 导入只实现 product + bom（routing 的 ERP 同步留后；routing 本地编辑页本期做）。
- 同步业务逻辑放 masterdata（upsert_product/upsert_bom）；integration 只解析文件→调 masterdata upsert，不碰 masterdata repository。
- 跨模块读 masterdata 走 `MasterDataService`/`MasterDataQueryService`；领域异常体系 + 全局 handler 沿用；事务边界 get_db；repository 只 flush。
- UI：HTMX 服务端渲染 + 薄荷绿卡片（复用 `.card`/`.data-table`/`.badge`/`.form-row` 等 app.css 样式）；第三方 JS 用本地 `/static/vendor/htmx.min.js`（无 CDN）；`{{ }}` 自动转义；写操作加 `require_login`（页面用 `current_user_or_none`→401+HX-Redirect；API 用 `Depends(require_login)`）；HTMX 写处理器吞 DomainError 前 `db.rollback()`。
- source=erp 记录：编辑表单里 ERP 管字段 `disabled`；列表/表单显示来源徽标（`.badge`，ERP 绿 + synced_at / 本地）。
- 提交前缀 `feat:`/`refactor:`/`chore:`/`test:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

P2b 结束时新增/修改：

```
src/lightmes/modules/masterdata/
├── models.py       # 改：Product/Bom/Routing 加 source/erp_ref/synced_at + erp_ref 部分唯一索引
├── schemas.py      # 改：*Read 加 source/erp_ref/synced_at；加 ProductUpsert/BomUpsert；LineRead 等已有
├── repository.py   # 改：Product/Bom/Routing repo 加 get_by_erp_ref；line/work_station list 已有
├── service.py      # 改：加 upsert_product/upsert_bom（同步逻辑）
├── query_service.py# （按需，读用）
└── router.py       # 改：补 line/work_station/sn_rule/bom/routing 管理页 + product 页增强来源徽标
src/lightmes/modules/production/
├── service.py      # sn_rule create 已有（P1a）；本期补 list
└── router.py       # 补 sn_rule 管理页（sn_rule 属 production 模块）
src/lightmes/modules/integration/          # 新模块
├── __init__.py     # register(app)
├── schemas.py      # SyncResult
├── service.py      # ErpSyncService 抽象 + FileErpSyncService（CSV/JSON 解析）
└── router.py       # 导入 API + 导入页面
src/lightmes/main.py                        # 改：注册 integration 模块
src/lightmes/migrations/versions/           # 新增：ERP 字段迁移
src/lightmes/templates/masterdata/          # 新增：lines/work_stations/sn_rules/boms/routings 页 + 增强 products
src/lightmes/templates/integration/         # 新增：import 页
src/lightmes/templates/home.html            # 改：导航扩展
tests/modules/masterdata/                    # upsert 测试 + 管理页测试
tests/modules/integration/                   # 同步/导入测试
```

> sn_rule 属于 production 模块（P1a 建的），其管理页放 production/router；其余主数据实体在 masterdata。

---

### Task 1: ERP 字段 + 迁移（product/bom/routing 加 source/erp_ref/synced_at）

给受 ERP 管辖的三张表加来源标记字段 + erp_ref 部分唯一索引。纯加字段，不改现有逻辑。

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`（Product/Bom/Routing 加字段 + 索引）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（ProductRead/BomRead/RoutingRead 加 source/erp_ref/synced_at）
- Create: `src/lightmes/migrations/versions/<auto>_add_erp_fields.py`
- Test: `tests/modules/masterdata/test_erp_fields.py`

**Interfaces:**
- Consumes: 现有 Product/Bom/Routing 模型。
- Produces:
  - Product/Bom/Routing 各加：`source: Mapped[str]`(default "manual", server_default "manual")、`erp_ref: Mapped[str | None]`(default None, index)、`synced_at: Mapped[datetime | None]`(default None)
  - 各加部分唯一索引：`Index("uq_products_erp_ref", "erp_ref", unique=True, postgresql_where=text("erp_ref IS NOT NULL"))`（bom→`uq_boms_erp_ref`，routing→`uq_routings_erp_ref`）
  - `ProductRead`/`BomRead`/`RoutingRead` 加 `source: str`、`erp_ref: str | None`、`synced_at: datetime | None`

- [ ] **Step 1: 加模型字段**

在 `masterdata/models.py`：顶部 import 确认含 `from datetime import datetime` 与 `DateTime`（若无则加 `from sqlalchemy import DateTime`；`Index`/`text` 已有）。给 `Product`、`Bom`、`Routing` 各加三字段（放在类体末尾）：
```python
    source: Mapped[str] = mapped_column(default="manual", server_default="manual")
    erp_ref: Mapped[str | None] = mapped_column(index=True, default=None)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```
并在各自 `__table_args__` 元组追加部分唯一索引（Product 当前无 __table_args__ → 新增；Bom/Routing 已有 __table_args__ 元组 → 追加一项）：
- Product: `__table_args__ = (Index("uq_products_erp_ref", "erp_ref", unique=True, postgresql_where=text("erp_ref IS NOT NULL")),)`
- Bom: 现有元组末尾加 `Index("uq_boms_erp_ref", "erp_ref", unique=True, postgresql_where=text("erp_ref IS NOT NULL"))`
- Routing: 现有元组末尾加 `Index("uq_routings_erp_ref", "erp_ref", unique=True, postgresql_where=text("erp_ref IS NOT NULL"))`

- [ ] **Step 2: schemas 加字段**

在 `masterdata/schemas.py`：`ProductRead`/`BomRead`/`RoutingRead` 各加（顶部 import 加 `from datetime import datetime`）：
```python
    source: str
    erp_ref: str | None
    synced_at: datetime | None
```

- [ ] **Step 3: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "add erp fields to product bom routing"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移给 products/boms/routings 各 add 三列（source NOT NULL server_default 'manual'、erp_ref nullable + 普通索引 ix_*_erp_ref、synced_at nullable）+ 三个部分唯一索引 uq_*_erp_ref。**打开迁移确认**：只动这三张表的这些列/索引，不误删 uq_active_*/uq_operation_*/uq_bom_item_* 等既有索引。若 autogenerate 顺序或多余 op，手工修正。

- [ ] **Step 4: 写测试**

`tests/modules/masterdata/test_erp_fields.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate
from lightmes.modules.masterdata.models import Product


def test_product_defaults_source_manual(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="ERP-P1", name="件", type="component"))
    assert p.source == "manual"
    assert p.erp_ref is None
    assert p.synced_at is None


def test_erp_ref_partial_unique(db_session):
    # 两条相同 erp_ref 的 product → 违反部分唯一索引
    db_session.add(Product(code="E1", name="a", type="component", source="erp", erp_ref="ERP-X"))
    db_session.flush()
    db_session.add(Product(code="E2", name="b", type="component", source="erp", erp_ref="ERP-X"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_null_erp_ref_not_constrained(db_session):
    # 多条 erp_ref=None（manual）互不冲突
    svc = MasterDataService(db_session)
    svc.create_product(ProductCreate(code="M1", name="a", type="component"))
    svc.create_product(ProductCreate(code="M2", name="b", type="component"))
    # 无异常即通过
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_erp_fields.py -v` → PASS（3）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata src/lightmes/migrations tests/modules/masterdata/test_erp_fields.py
git commit -m "feat: add ERP source/erp_ref/synced_at fields to product/bom/routing"
```

---

### Task 2: masterdata.upsert_product（同步核心逻辑）

按 erp_ref upsert product：存在更新 ERP 管字段+synced_at，不存在新建 source=erp；不覆盖 manual；幂等。

**Files:**
- Modify: `src/lightmes/modules/masterdata/repository.py`（ProductRepository 加 get_by_erp_ref）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（加 ProductUpsert）
- Modify: `src/lightmes/modules/masterdata/service.py`（加 upsert_product）
- Test: `tests/modules/masterdata/test_upsert_product.py`

**Interfaces:**
- Consumes: Product 模型（Task 1 字段）。
- Produces:
  - `ProductRepository.get_by_erp_ref(erp_ref: str) -> Product | None`
  - `schemas.ProductUpsert`（`erp_ref: str`, `code: str`, `name: str`, `type: str`, `unit: str = "pcs"`, `track_mode: str = "none"`, `spec: str | None = None`）
  - `MasterDataService.upsert_product(data: ProductUpsert) -> tuple[Product, str]`（返回 (obj, "created"|"updated")）：按 erp_ref 查；存在→更新 code/name/type/unit/track_mode/spec + synced_at=now(utc)，返回 (obj,"updated")；不存在→新建 source="erp", erp_ref, synced_at=now, 返回 (obj,"created")。

- [ ] **Step 1: repository 加 get_by_erp_ref**

在 `masterdata/repository.py` `ProductRepository` 加（确认 import 有 `select`）：
```python
    def get_by_erp_ref(self, erp_ref: str) -> Product | None:
        return self.db.execute(
            select(Product).where(Product.erp_ref == erp_ref)
        ).scalar_one_or_none()
```

- [ ] **Step 2: schema**

在 `masterdata/schemas.py` 加：
```python
class ProductUpsert(BaseModel):
    erp_ref: str
    code: str
    name: str
    type: str
    unit: str = "pcs"
    track_mode: str = "none"
    spec: str | None = None
```

- [ ] **Step 3: 写失败测试**

`tests/modules/masterdata/test_upsert_product.py`:
```python
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductUpsert, ProductCreate


def test_upsert_creates_new_erp_product(db_session):
    svc = MasterDataService(db_session)
    obj, action = svc.upsert_product(ProductUpsert(
        erp_ref="ERP-1", code="P-1", name="件A", type="component"))
    assert action == "created"
    assert obj.source == "erp"
    assert obj.erp_ref == "ERP-1"
    assert obj.synced_at is not None


def test_upsert_updates_existing_by_erp_ref(db_session):
    svc = MasterDataService(db_session)
    svc.upsert_product(ProductUpsert(erp_ref="ERP-2", code="P-2", name="旧名", type="component"))
    obj, action = svc.upsert_product(ProductUpsert(
        erp_ref="ERP-2", code="P-2", name="新名", type="component"))
    assert action == "updated"
    assert obj.name == "新名"


def test_upsert_idempotent(db_session):
    svc = MasterDataService(db_session)
    o1, a1 = svc.upsert_product(ProductUpsert(erp_ref="ERP-3", code="P-3", name="x", type="component"))
    o2, a2 = svc.upsert_product(ProductUpsert(erp_ref="ERP-3", code="P-3", name="x", type="component"))
    assert a1 == "created" and a2 == "updated"
    assert o1.id == o2.id  # 同一条，不重复


def test_upsert_does_not_touch_manual_product(db_session):
    svc = MasterDataService(db_session)
    # 手动建一个 code=SHARED 的 manual 产品（erp_ref 空）
    manual = svc.create_product(ProductCreate(code="SHARED", name="本地", type="component"))
    # ERP 导入一个不同 erp_ref 的产品（即使 code 相似也按 erp_ref 匹配，不会命中 manual）
    obj, action = svc.upsert_product(ProductUpsert(
        erp_ref="ERP-9", code="ERP-CODE", name="ERP件", type="component"))
    assert action == "created"
    # manual 未被改动
    assert svc.products.get(manual.id).source == "manual"
    assert svc.products.get(manual.id).name == "本地"
```

- [ ] **Step 4: 运行确认失败，写 upsert_product**

在 `masterdata/service.py`：顶部 import 加 `from datetime import datetime, timezone` 与 `ProductUpsert`（schemas）。加方法：
```python
    def upsert_product(self, data: "ProductUpsert") -> tuple[Product, str]:
        existing = self.products.get_by_erp_ref(data.erp_ref)
        if existing is not None:
            existing.code = data.code
            existing.name = data.name
            existing.type = data.type
            existing.unit = data.unit
            existing.track_mode = data.track_mode
            existing.spec = data.spec
            existing.synced_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing, "updated"
        product = Product(
            code=data.code, name=data.name, type=data.type, unit=data.unit,
            track_mode=data.track_mode, spec=data.spec,
            source="erp", erp_ref=data.erp_ref,
            synced_at=datetime.now(timezone.utc),
        )
        return self.products.add(product), "created"
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run → PASS（4）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata tests/modules/masterdata/test_upsert_product.py
git commit -m "feat: add upsert_product ERP sync logic (by erp_ref, tag, idempotent)"
```

---

### Task 3: masterdata.upsert_bom（JSON 结构，组件 code 解析）

按 erp_ref upsert BOM：组件用 product code 解析（找不到→可捕获错误）；存在替换 items+synced_at，不存在新建 source=erp。

**Files:**
- Modify: `src/lightmes/modules/masterdata/repository.py`（BomRepository 加 get_by_erp_ref、replace_items）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（加 BomUpsert/BomItemUpsert）
- Modify: `src/lightmes/modules/masterdata/service.py`（加 upsert_bom）
- Test: `tests/modules/masterdata/test_upsert_bom.py`

**Interfaces:**
- Consumes: Bom/BomItem/Product；ProductRepository.get_by_code。
- Produces:
  - `BomRepository.get_by_erp_ref(erp_ref) -> Bom | None`
  - `BomRepository.delete_items(bom_id: int) -> None`（删该 bom 全部 item，供替换）
  - `schemas.BomItemUpsert`（`component_code: str`, `qty: float = 1`）、`BomUpsert`（`erp_ref: str`, `product_code: str`, `items: list[BomItemUpsert]`）
  - `MasterDataService.upsert_bom(data: BomUpsert) -> tuple[Bom, str]`：解析 product_code→成品（找不到→ValueError）；每个 component_code→组件 product（找不到→ValueError）；按 erp_ref 查 bom，存在→删旧 items 建新 items + synced_at，返回 (obj,"updated")；不存在→新建 source="erp" bom + items，返回 (obj,"created")。item.track_mode 取组件 product.track_mode（沿用 create_bom）。

- [ ] **Step 1: repository**

在 `BomRepository` 加：
```python
    def get_by_erp_ref(self, erp_ref: str) -> Bom | None:
        return self.db.execute(
            select(Bom).where(Bom.erp_ref == erp_ref)
        ).scalar_one_or_none()

    def delete_items(self, bom_id: int) -> None:
        for it in self.items_of(bom_id):
            self.db.delete(it)
        self.db.flush()
```

- [ ] **Step 2: schemas**

```python
class BomItemUpsert(BaseModel):
    component_code: str
    qty: float = 1


class BomUpsert(BaseModel):
    erp_ref: str
    product_code: str
    items: list[BomItemUpsert]
```

- [ ] **Step 3: 写失败测试**

`tests/modules/masterdata/test_upsert_bom.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate, BomUpsert, BomItemUpsert


def _products(db_session):
    svc = MasterDataService(db_session)
    svc.create_product(ProductCreate(code="FIN", name="成品", type="finished"))
    svc.create_product(ProductCreate(code="C1", name="主板", type="component", track_mode="serial"))
    svc.create_product(ProductCreate(code="C2", name="螺丝", type="consumable", track_mode="batch"))
    return svc


def test_upsert_bom_creates(db_session):
    svc = _products(db_session)
    bom, action = svc.upsert_bom(BomUpsert(erp_ref="EB-1", product_code="FIN", items=[
        BomItemUpsert(component_code="C1", qty=1),
        BomItemUpsert(component_code="C2", qty=4)]))
    assert action == "created"
    assert bom.source == "erp"
    items = svc.boms.items_of(bom.id)
    assert {i.track_mode for i in items} == {"serial", "batch"}


def test_upsert_bom_replaces_items_on_update(db_session):
    svc = _products(db_session)
    svc.upsert_bom(BomUpsert(erp_ref="EB-2", product_code="FIN", items=[
        BomItemUpsert(component_code="C1", qty=1)]))
    bom, action = svc.upsert_bom(BomUpsert(erp_ref="EB-2", product_code="FIN", items=[
        BomItemUpsert(component_code="C2", qty=8)]))
    assert action == "updated"
    items = svc.boms.items_of(bom.id)
    assert len(items) == 1 and items[0].qty == 8


def test_upsert_bom_unknown_product_raises(db_session):
    svc = _products(db_session)
    with pytest.raises(ValueError):
        svc.upsert_bom(BomUpsert(erp_ref="EB-3", product_code="NOPE", items=[
            BomItemUpsert(component_code="C1")]))


def test_upsert_bom_unknown_component_raises(db_session):
    svc = _products(db_session)
    with pytest.raises(ValueError):
        svc.upsert_bom(BomUpsert(erp_ref="EB-4", product_code="FIN", items=[
            BomItemUpsert(component_code="NOPE")]))
```

- [ ] **Step 4: 运行确认失败，写 upsert_bom**

在 `masterdata/service.py`（import 加 `BomUpsert`）：
```python
    def upsert_bom(self, data: "BomUpsert") -> tuple[Bom, str]:
        product = self.products.get_by_code(data.product_code)
        if product is None:
            raise ValueError(f"成品不存在: {data.product_code}")
        resolved = []
        for it in data.items:
            comp = self.products.get_by_code(it.component_code)
            if comp is None:
                raise ValueError(f"组件不存在: {it.component_code}")
            resolved.append((comp, it.qty))
        existing = self.boms.get_by_erp_ref(data.erp_ref)
        if existing is not None:
            self.boms.delete_items(existing.id)
            for comp, qty in resolved:
                self.db.add(BomItem(bom_id=existing.id,
                    component_product_id=comp.id, qty=qty, track_mode=comp.track_mode))
            existing.synced_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing, "updated"
        bom = Bom(product_id=product.id, source="erp", erp_ref=data.erp_ref,
                  synced_at=datetime.now(timezone.utc))
        self.boms.add(bom)
        for comp, qty in resolved:
            self.db.add(BomItem(bom_id=bom.id, component_product_id=comp.id,
                qty=qty, track_mode=comp.track_mode))
        self.db.flush()
        return bom, "created"
```
（`datetime/timezone` 已在 Task 2 import。）

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run → PASS（4）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata tests/modules/masterdata/test_upsert_bom.py
git commit -m "feat: add upsert_bom ERP sync logic (by erp_ref, component code resolution, item replace)"
```

---

### Task 4: integration 模块 + ErpSyncService 抽象 + FileErpSyncService

新建 integration 模块：同步结果 schema、抽象基类、文件导入实现（product CSV / bom JSON），部分成功+错误报告。

**Files:**
- Create: `src/lightmes/modules/integration/__init__.py`, `schemas.py`, `service.py`
- Test: `tests/modules/integration/__init__.py`, `tests/modules/integration/test_file_sync.py`

**Interfaces:**
- Consumes: `MasterDataService.upsert_product/upsert_bom`, `ProductUpsert`, `BomUpsert`, `BomItemUpsert`。
- Produces:
  - `integration.schemas.SyncResult`（`created: int = 0`, `updated: int = 0`, `skipped: int = 0`, `errors: list[str] = []`）
  - `integration.service.ErpSyncService`（ABC，抽象方法 `sync_products(raw: bytes) -> SyncResult`、`sync_boms(raw: bytes) -> SyncResult`）
  - `integration.service.FileErpSyncService(db)`（继承 ErpSyncService）：
    - `sync_products(raw)`：解析 CSV（`csv.DictReader`，列 erp_ref,code,name,type,spec,unit,track_mode；unit/track_mode 缺省用默认），逐行构造 ProductUpsert 调 upsert_product；action created/updated 累加；行异常（缺列/upsert ValueError）→ errors.append(f"行 {n}: {e}") 且 skipped+1，不中断。
    - `sync_boms(raw)`：解析 JSON（list of {erp_ref, product_code, items:[{component_code, qty}]}），逐条调 upsert_bom；条异常→errors+skipped。

- [ ] **Step 1: 模块骨架 + schemas**

`src/lightmes/modules/integration/__init__.py`:
```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from lightmes.modules.integration.router import router
    app.include_router(router)
```
`src/lightmes/modules/integration/schemas.py`:
```python
from pydantic import BaseModel, Field


class SyncResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
```
`tests/modules/integration/__init__.py`: 空文件。

- [ ] **Step 2: 写失败测试**

`tests/modules/integration/test_file_sync.py`:
```python
from lightmes.modules.integration.service import FileErpSyncService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import ProductCreate


PRODUCT_CSV = b"""erp_ref,code,name,type,spec,unit,track_mode
ERP-P1,P-1,\xe4\xbb\xb6A,component,,pcs,serial
ERP-P2,P-2,\xe4\xbb\xb6B,component,,pcs,batch
"""

def test_sync_products_csv_created_then_idempotent(db_session):
    svc = FileErpSyncService(db_session)
    r1 = svc.sync_products(PRODUCT_CSV)
    assert r1.created == 2 and r1.updated == 0 and not r1.errors
    r2 = svc.sync_products(PRODUCT_CSV)  # 重复导入
    assert r2.created == 0 and r2.updated == 2  # 幂等：全部 updated

def test_sync_products_bad_row_partial_success(db_session):
    bad = b"erp_ref,code,name,type\nERP-A,A,\xe5\xa5\xbd,component\n,,,\n"  # 第2行缺 erp_ref
    svc = FileErpSyncService(db_session)
    r = svc.sync_products(bad)
    assert r.created == 1
    assert r.skipped == 1 and len(r.errors) == 1  # 坏行跳过，好行照常

def test_sync_boms_json(db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1", name="主板", type="component", track_mode="serial"))
    import json
    payload = json.dumps([{"erp_ref": "EB-1", "product_code": "FIN",
        "items": [{"component_code": "C1", "qty": 1}]}]).encode()
    r = FileErpSyncService(db_session).sync_boms(payload)
    assert r.created == 1 and not r.errors

def test_sync_boms_unknown_component_partial(db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN2", name="成品", type="finished"))
    import json
    payload = json.dumps([{"erp_ref": "EB-2", "product_code": "FIN2",
        "items": [{"component_code": "NOPE", "qty": 1}]}]).encode()
    r = FileErpSyncService(db_session).sync_boms(payload)
    assert r.created == 0 and r.skipped == 1 and len(r.errors) == 1
```

- [ ] **Step 3: 运行确认失败，写 service**

`src/lightmes/modules/integration/service.py`:
```python
import csv
import io
import json
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from lightmes.modules.integration.schemas import SyncResult
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductUpsert, BomUpsert, BomItemUpsert,
)


class ErpSyncService(ABC):
    @abstractmethod
    def sync_products(self, raw: bytes) -> SyncResult: ...

    @abstractmethod
    def sync_boms(self, raw: bytes) -> SyncResult: ...


class FileErpSyncService(ErpSyncService):
    """从上传文件导入（模拟金蝶下发）。product→CSV，bom→JSON。
    将来接金蝶 = 另写 KingdeeErpSyncService 读 API，复用 masterdata upsert 逻辑。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.md = MasterDataService(db)

    def sync_products(self, raw: bytes) -> SyncResult:
        result = SyncResult()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for n, row in enumerate(reader, start=2):  # 表头是第1行
            try:
                if not (row.get("erp_ref") or "").strip():
                    raise ValueError("缺少 erp_ref")
                data = ProductUpsert(
                    erp_ref=row["erp_ref"].strip(),
                    code=(row.get("code") or "").strip(),
                    name=(row.get("name") or "").strip(),
                    type=(row.get("type") or "").strip(),
                    unit=(row.get("unit") or "pcs").strip() or "pcs",
                    track_mode=(row.get("track_mode") or "none").strip() or "none",
                    spec=(row.get("spec") or "").strip() or None,
                )
                _, action = self.md.upsert_product(data)
                if action == "created":
                    result.created += 1
                else:
                    result.updated += 1
            except Exception as e:  # 部分成功：坏行跳过
                result.skipped += 1
                result.errors.append(f"行 {n}: {e}")
        return result

    def sync_boms(self, raw: bytes) -> SyncResult:
        result = SyncResult()
        try:
            records = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            result.errors.append(f"JSON 解析失败: {e}")
            return result
        for i, rec in enumerate(records, start=1):
            try:
                data = BomUpsert(
                    erp_ref=rec["erp_ref"],
                    product_code=rec["product_code"],
                    items=[BomItemUpsert(component_code=it["component_code"],
                                         qty=it.get("qty", 1)) for it in rec["items"]],
                )
                _, action = self.md.upsert_bom(data)
                if action == "created":
                    result.created += 1
                else:
                    result.updated += 1
            except Exception as e:
                result.skipped += 1
                result.errors.append(f"第 {i} 条: {e}")
        return result
```
说明：`raw` bytes 用 utf-8-sig 解码（容忍 Excel 导出的 BOM 头）。逐行/条 try，坏的记 errors+skipped，不中断（部分成功）。

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/integration/test_file_sync.py -v` → PASS（4）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/integration tests/modules/integration
git commit -m "feat: add integration module with ErpSyncService and FileErpSyncService"
```

---

### Task 5: integration 导入 API + 导入页面

把 FileErpSyncService 接到 HTTP：上传文件 → 显示 SyncResult。

**Files:**
- Create: `src/lightmes/modules/integration/router.py`
- Create: `src/lightmes/templates/integration/import.html`, `src/lightmes/templates/integration/partials/sync_result.html`
- Modify: `src/lightmes/main.py`（注册 integration）
- Test: `tests/modules/integration/test_import_pages.py`

**Interfaces:**
- Consumes: `FileErpSyncService`, `SyncResult`, `require_login`, `current_user_or_none`, `get_db`。
- Produces:
  - `POST /api/integration/import/products`（multipart file）→ 200 `SyncResult`（require_login）
  - `POST /api/integration/import/boms`（multipart file）→ 200 `SyncResult`（require_login）
  - `GET /integration/import` → 导入页 HTML
  - `POST /integration/import`（HTMX form：`kind`(products|boms) + file）→ 结果片段（未登录 401+HX-Redirect）
  - `integration.register(app)` 已在 __init__（Task 4）；main.py 调 `integration.register(app)`

- [ ] **Step 1: 写模板**

`src/lightmes/templates/integration/import.html`:
```html
{% extends "base.html" %}
{% block title %}主数据导入{% endblock %}
{% block content %}
<h1 class="page-title">主数据导入 <small>模拟 ERP 下发</small></h1>
<div class="card">
  <div class="card__title">上传文件</div>
  <form class="form-row" hx-post="/integration/import" hx-target="#result" hx-swap="innerHTML"
        hx-encoding="multipart/form-data">
    <div class="field"><label>类型</label>
      <select name="kind">
        <option value="products">产品 (CSV)</option>
        <option value="boms">BOM (JSON)</option>
      </select>
    </div>
    <div class="field" style="flex:1"><label>文件</label><input type="file" name="file" required></div>
    <button type="submit">导入</button>
  </form>
  <div id="result" class="result-slot"></div>
</div>
{% endblock %}
```
`src/lightmes/templates/integration/partials/sync_result.html`:
```html
<div class="alert {% if result.errors %}alert--warn{% else %}alert--ok{% endif %}">
  新增 {{ result.created }} · 更新 {{ result.updated }} · 跳过 {{ result.skipped }}
</div>
{% if result.errors %}
<ul>{% for e in result.errors %}<li style="color:var(--danger)">{{ e }}</li>{% endfor %}</ul>
{% endif %}
```
（`.alert--warn` 若 app.css 无则用 `.alert--danger`；实现时确认样式类存在，否则用现有类。）

- [ ] **Step 2: 写失败测试**

`tests/modules/integration/test_import_pages.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(
        UserCreate(username="imp", password="pw12345", display_name="Imp"))
    db_session.flush()
    assert client.post("/login", data={"username": "imp", "password": "pw12345"}).status_code == 200


def test_import_page_renders(client, db_session):
    resp = client.get("/integration/import")
    assert resp.status_code == 200
    assert "主数据导入" in resp.text


def test_import_products_api_requires_login(client, db_session):
    resp = client.post("/api/integration/import/products",
        files={"file": ("p.csv", b"erp_ref,code,name,type\n", "text/csv")})
    assert resp.status_code == 401


def test_import_products_page_success(client, db_session):
    _login(client, db_session)
    csv = b"erp_ref,code,name,type,unit,track_mode\nERP-1,P1,\xe4\xbb\xb6,component,pcs,serial\n"
    resp = client.post("/integration/import",
        data={"kind": "products"}, files={"file": ("p.csv", csv, "text/csv")})
    assert resp.status_code == 200
    assert "新增 1" in resp.text


def test_import_page_requires_login_on_post(client, db_session):
    resp = client.post("/integration/import",
        data={"kind": "products"}, files={"file": ("p.csv", b"x", "text/csv")})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"
```

- [ ] **Step 3: 运行确认失败，写 router**

`src/lightmes/modules/integration/router.py`:
```python
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_login
from lightmes.modules.auth.models import User
from lightmes.modules.integration.schemas import SyncResult
from lightmes.modules.integration.service import FileErpSyncService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.post("/api/integration/import/products", response_model=SyncResult)
async def api_import_products(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> SyncResult:
    raw = await file.read()
    return FileErpSyncService(db).sync_products(raw)


@router.post("/api/integration/import/boms", response_model=SyncResult)
async def api_import_boms(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> SyncResult:
    raw = await file.read()
    return FileErpSyncService(db).sync_boms(raw)


@router.get("/integration/import", response_class=HTMLResponse)
def import_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "integration/import.html")


@router.post("/integration/import", response_class=HTMLResponse)
async def import_submit(
    request: Request,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    raw = await file.read()
    svc = FileErpSyncService(db)
    result = svc.sync_products(raw) if kind == "products" else svc.sync_boms(raw)
    return templates.TemplateResponse(
        request, "integration/partials/sync_result.html", {"result": result}
    )
```
在 `src/lightmes/main.py`：import 加 `from lightmes.modules import integration`（或并入现有 `from lightmes.modules import auth, masterdata, production, trace`），在 `trace.register(app)` 下方加 `integration.register(app)`。

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run → PASS（4）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/integration src/lightmes/templates/integration src/lightmes/main.py tests/modules/integration/test_import_pages.py
git commit -m "feat: add ERP import API and HTMX import page"
```

---

### Task 6: 补齐 line/work_station/sn_rule 管理页

给产线、作业站、SN规则加最简管理页（列表+新增），复用 product 页模式。

**Files:**
- Modify: `src/lightmes/modules/masterdata/router.py`（lines/work-stations 页面 + list API 若缺）
- Modify: `src/lightmes/modules/production/router.py`（sn-rules 页面）
- Modify: `src/lightmes/modules/production/service.py`（若无 list_sn_rules 则加）+ `repository.py`（SnRuleRepository.list_all）
- Create: `src/lightmes/templates/masterdata/lines.html`, `work_stations.html`, `src/lightmes/templates/production/sn_rules.html` + 各 partials 行模板
- Test: `tests/modules/masterdata/test_masterdata_pages.py`（扩展）, `tests/modules/production/test_sn_rule_pages.py`

**Interfaces:**
- Consumes: `MasterDataService.create_line/create_work_station`, `LineRepository.list_all`, `WorkStationRepository`（需 list_all——若无则加）, `ProductionService.create_sn_rule`, `SnRuleRepository`（需 list_all——加）。
- Produces:
  - `WorkStationRepository.list_all() -> list[WorkStation]`（若缺）
  - `SnRuleRepository.list_all() -> list[SnRule]`（加）
  - masterdata router：`GET/POST /masterdata/lines`、`GET/POST /masterdata/work-stations`
  - production router：`GET/POST /production/sn-rules`
  - 各页 HTMX：新增后追加行片段；失败红片段；写操作 require_login（页面用 current_user_or_none→401+HX-Redirect，沿用 product 页模式）

- [ ] **Step 1: repository list 方法（若缺）**

`masterdata/repository.py` `WorkStationRepository` 加（若无 list_all）：
```python
    def list_all(self) -> list[WorkStation]:
        return list(self.db.execute(
            select(WorkStation).order_by(WorkStation.line_id, WorkStation.seq)
        ).scalars().all())
```
`production/repository.py` `SnRuleRepository` 加：
```python
    def list_all(self) -> list[SnRule]:
        return list(self.db.execute(select(SnRule)).scalars().all())
```
（确认 select 已 import。）

- [ ] **Step 2: 写模板**

`src/lightmes/templates/masterdata/lines.html`（复用 product 页结构）:
```html
{% extends "base.html" %}
{% block title %}产线管理{% endblock %}
{% block content %}
<h1 class="page-title">产线管理</h1>
<div class="card">
  <div class="card__title">新增产线</div>
  <form class="form-row" hx-post="/masterdata/lines" hx-target="#rows" hx-swap="beforeend"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="field"><label>编码</label><input name="code" required></div>
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field" style="flex:1"><label>描述</label><input name="description"></div>
    <button type="submit">新增</button>
  </form>
</div>
<div class="card">
  <div class="card__title">产线列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>描述</th></tr></thead>
    <tbody id="rows">
      {% for line in lines %}{% include "masterdata/partials/line_row.html" %}{% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```
`src/lightmes/templates/masterdata/partials/line_row.html`:
```html
<tr><td>{{ line.id }}</td><td>{{ line.code }}</td><td>{{ line.name }}</td><td>{{ line.description or "" }}</td></tr>
```
`work_stations.html`（含产线下拉 + seq）:
```html
{% extends "base.html" %}
{% block title %}作业站管理{% endblock %}
{% block content %}
<h1 class="page-title">作业站管理</h1>
<div class="card">
  <div class="card__title">新增作业站</div>
  <form class="form-row" hx-post="/masterdata/work-stations" hx-target="#rows" hx-swap="beforeend"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="field"><label>编码</label><input name="code" required></div>
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field"><label>产线</label>
      <select name="line_id">{% for l in lines %}<option value="{{ l.id }}">{{ l.code }} {{ l.name }}</option>{% endfor %}</select>
    </div>
    <div class="field"><label>顺序</label><input name="seq" type="number" required></div>
    <button type="submit">新增</button>
  </form>
</div>
<div class="card">
  <div class="card__title">作业站列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>产线</th><th>顺序</th></tr></thead>
    <tbody id="rows">
      {% for ws in work_stations %}{% include "masterdata/partials/work_station_row.html" %}{% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```
`partials/work_station_row.html`:
```html
<tr><td>{{ ws.id }}</td><td>{{ ws.code }}</td><td>{{ ws.name }}</td><td>{{ ws.line_id }}</td><td>{{ ws.seq }}</td></tr>
```
`src/lightmes/templates/production/sn_rules.html`:
```html
{% extends "base.html" %}
{% block title %}SN规则管理{% endblock %}
{% block content %}
<h1 class="page-title">SN 规则管理</h1>
<div class="card">
  <div class="card__title">新增 SN 规则</div>
  <form class="form-row" hx-post="/production/sn-rules" hx-target="#rows" hx-swap="beforeend"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="field"><label>编码</label><input name="code" required></div>
    <div class="field"><label>名称</label><input name="name" required></div>
    <div class="field" style="flex:1"><label>模板</label><input name="pattern" placeholder="SN{YY}{MM}{DD}{SEQ:5}" required></div>
    <div class="field"><label>重置</label>
      <select name="seq_reset"><option value="never">不重置</option><option value="daily">按日</option><option value="monthly">按月</option></select>
    </div>
    <button type="submit">新增</button>
  </form>
</div>
<div class="card">
  <div class="card__title">SN 规则列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>模板</th><th>重置</th></tr></thead>
    <tbody id="rows">
      {% for r in rules %}{% include "production/partials/sn_rule_row.html" %}{% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```
`production/partials/sn_rule_row.html`:
```html
<tr><td>{{ r.id }}</td><td>{{ r.code }}</td><td>{{ r.name }}</td><td>{{ r.pattern }}</td><td>{{ r.seq_reset }}</td></tr>
```

- [ ] **Step 3: 写失败测试**

`tests/modules/production/test_sn_rule_pages.py`（及 masterdata 页测试同理，用 TestClient+登录 fixture，参考 test_import_pages.py 的 client/_login）:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="u", password="pw12345", display_name="U"))
    db_session.flush()
    client.post("/login", data={"username": "u", "password": "pw12345"})


def test_lines_page_and_create(client, db_session):
    _login(client, db_session)
    assert client.get("/masterdata/lines").status_code == 200
    resp = client.post("/masterdata/lines", data={"code": "L1", "name": "线1", "description": ""})
    assert resp.status_code == 200 and "L1" in resp.text


def test_sn_rules_page_and_create(client, db_session):
    _login(client, db_session)
    assert client.get("/production/sn-rules").status_code == 200
    resp = client.post("/production/sn-rules",
        data={"code": "R1", "name": "规则", "pattern": "SN{SEQ:5}", "seq_reset": "never"})
    assert resp.status_code == 200 and "R1" in resp.text


def test_lines_create_requires_login(client, db_session):
    resp = client.post("/masterdata/lines", data={"code": "X", "name": "x", "description": ""})
    assert resp.status_code == 401
```

- [ ] **Step 4: 运行确认失败，写页面路由**

在 `masterdata/router.py` 加 lines/work-stations 页面路由（参考现有 product 页 `products_page`/`products_create_page` 的 current_user_or_none + 片段模式）。work-stations 页 GET 需传 `lines` 给下拉。POST 用 Form 收字段调 `create_line`/`create_work_station`，成功渲染 row 片段，失败渲染红片段，未登录 401+HX-Redirect。
在 `production/router.py` 加 sn-rules 页面路由（同模式，调 `ProductionService.create_sn_rule`）。
（完整代码参照 masterdata/router.py 现有 products 页处理器逐一实现，字段替换为对应实体。）

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run 各页测试 → PASS。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata src/lightmes/modules/production src/lightmes/templates tests/modules
git commit -m "feat: add line/work_station/sn_rule management pages"
```

---

### Task 7: routing 编辑页 + product 页来源徽标 + bom 查看页

最复杂的路线编辑页（选产品+加工序行）；product 列表加来源徽标；bom 列表/查看页。

**Files:**
- Modify: `src/lightmes/modules/masterdata/router.py`（routings 页、boms 页、products 页增强）
- Create: `src/lightmes/templates/masterdata/routings.html`, `boms.html`
- Modify: `src/lightmes/templates/masterdata/products.html` + `partials/product_row.html`（来源徽标）
- Test: `tests/modules/masterdata/test_routing_bom_pages.py`

**Interfaces:**
- Consumes: `MasterDataService.create_routing`（含 operations）, `RoutingRepository.list_all/operations_of`, `WorkStationRepository.list_all`, `ProductRepository.list_all`, `BomRepository.list_all/items_of`。
- Produces:
  - `GET /masterdata/routings` → 路线编辑页（选产品下拉 + 动态加工序行：seq/code/name/选作业站下拉；提交建路线）
  - `POST /masterdata/routings`（Form：product_id, code, name, + 多组 operation 字段）→ 结果片段
  - `GET /masterdata/boms` → BOM 列表/查看（成品→组件行，来源徽标）
  - product 列表行加来源徽标（source=erp 绿 badge + synced_at；manual "本地"）
  - `RoutingRepository.list_all()`、`ProductRepository.list_all()`、`BomRepository.list_all()` 若缺则加

- [ ] **Step 1: repository list 方法（若缺）**

`masterdata/repository.py`：`RoutingRepository.list_all`、`BomRepository.list_all`（ProductRepository.list_all P1a 已有；确认）：
```python
    # RoutingRepository
    def list_all(self) -> list[Routing]:
        return list(self.db.execute(select(Routing)).scalars().all())
    # BomRepository
    def list_all(self) -> list[Bom]:
        return list(self.db.execute(select(Bom)).scalars().all())
```

- [ ] **Step 2: product 行来源徽标**

`masterdata/partials/product_row.html` 末尾加一列来源：
```html
<td>{% if product.source == "erp" %}<span class="badge">ERP</span>{% else %}<span class="badge badge--muted">本地</span>{% endif %}</td>
```
`products.html` 表头 `<thead>` 加 `<th>来源</th>`（与列对齐）。（`.badge--muted` 若无则去掉修饰类用纯 `.badge`。）

- [ ] **Step 3: routing 编辑页模板**

`src/lightmes/templates/masterdata/routings.html`（MVP：固定几行工序输入，或用简单 HTMX "加一行"；本期用固定 3 行工序输入槽最简可用，多余留空跳过）:
```html
{% extends "base.html" %}
{% block title %}工艺路径管理{% endblock %}
{% block content %}
<h1 class="page-title">工艺路径管理</h1>
<div class="card">
  <div class="card__title">新建工艺路径</div>
  <form hx-post="/masterdata/routings" hx-target="#result" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="form-row">
      <div class="field"><label>路线编码</label><input name="code" required></div>
      <div class="field"><label>名称</label><input name="name" required></div>
      <div class="field"><label>产品</label>
        <select name="product_id">{% for p in products %}<option value="{{ p.id }}">{{ p.code }} {{ p.name }}</option>{% endfor %}</select>
      </div>
    </div>
    <p class="nav-card__desc" style="margin:12px 0 6px">工序（按 seq 顺序，作业站从下拉选；留空的行忽略）</p>
    {% for i in range(1, 6) %}
    <div class="form-row">
      <div class="field"><label>seq</label><input name="op_seq" type="number" value="{{ i }}"></div>
      <div class="field"><label>工序码</label><input name="op_code" placeholder="可留空"></div>
      <div class="field"><label>工序名</label><input name="op_name" placeholder="可留空"></div>
      <div class="field"><label>作业站</label>
        <select name="op_ws"><option value="">--</option>{% for w in work_stations %}<option value="{{ w.id }}">{{ w.code }}</option>{% endfor %}</select>
      </div>
    </div>
    {% endfor %}
    <button type="submit">保存路线</button>
  </form>
  <div id="result" class="result-slot"></div>
</div>
<div class="card">
  <div class="card__title">路线列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>产品</th><th>状态</th><th>来源</th></tr></thead>
    <tbody>
      {% for r in routings %}
      <tr><td>{{ r.id }}</td><td>{{ r.code }}</td><td>{{ r.name }}</td><td>{{ r.product_id }}</td>
      <td>{{ r.status }}</td><td>{% if r.source=="erp" %}ERP{% else %}本地{% endif %}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```
说明：MVP 用 5 个固定工序输入槽（op_seq/op_code/op_name/op_ws 各为重复 name 的多值 Form 字段），POST 处理器按 zip 组装、跳过 code 或 ws 为空的行。动态"加行"留后增强。

`boms.html`（列表+查看，来源徽标）:
```html
{% extends "base.html" %}
{% block title %}BOM 管理{% endblock %}
{% block content %}
<h1 class="page-title">BOM 管理 <small>ERP 同步 / 本地</small></h1>
<div class="card">
  <div class="card__title">BOM 列表</div>
  <table class="data-table">
    <thead><tr><th>ID</th><th>成品</th><th>版本</th><th>状态</th><th>来源</th></tr></thead>
    <tbody>
      {% for b in boms %}
      <tr><td>{{ b.id }}</td><td>{{ b.product_id }}</td><td>{{ b.version }}</td><td>{{ b.status }}</td>
      <td>{% if b.source=="erp" %}<span class="badge">ERP</span>{% else %}<span class="badge">本地</span>{% endif %}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: 写失败测试**

`tests/modules/masterdata/test_routing_bom_pages.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="rb", password="pw12345", display_name="Rb"))
    db_session.flush()
    client.post("/login", data={"username": "rb", "password": "pw12345"})


def test_routing_page_and_create(client, db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="RP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="RL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="RW", name="站", line_id=line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    assert client.get("/masterdata/routings").status_code == 200
    resp = client.post("/masterdata/routings", data=[
        ("code", "RT1"), ("name", "路线"), ("product_id", str(p.id)),
        ("op_seq", "1"), ("op_code", "OP1"), ("op_name", "上料"), ("op_ws", str(w.id)),
        ("op_seq", "2"), ("op_code", ""), ("op_name", ""), ("op_ws", ""),  # 空行忽略
    ])
    assert resp.status_code == 200
    assert "RT1" in resp.text or "保存" in resp.text or "成功" in resp.text


def test_products_page_shows_source_badge(client, db_session):
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="BADGE-M", name="本地件", type="component"))
    _login(client, db_session)
    resp = client.get("/masterdata/products")
    assert resp.status_code == 200
    assert "本地" in resp.text  # 来源徽标


def test_boms_page_renders(client, db_session):
    _login(client, db_session)
    assert client.get("/masterdata/boms").status_code == 200
```

- [ ] **Step 5: 运行确认失败，写路由**

`masterdata/router.py`：
- `products_page` 传的 product 已含 source（模型字段），模板加徽标即可（若 products_page 处理器已存在，仅改模板；确认列表查询返回含 source）。
- `GET /masterdata/routings`：查 products + work_stations + routings 传模板。
- `POST /masterdata/routings`：用 `Form(...)` 收 `code`/`name`/`product_id` + 多值 `op_seq: list[str]`/`op_code`/`op_name`/`op_ws`（FastAPI 多同名字段 → `list`）。zip 组装 operations，跳过 op_code 或 op_ws 为空的行，构造 `RoutingCreate(operations=[OperationCreate(...)])` 调 `create_routing`；成功绿片段，失败（含 ValueError）先 db.rollback() 再红片段；未登录 401+HX-Redirect。
- `GET /masterdata/boms`：查 boms 传模板。
（完整处理器参照现有 products 页 + Task 6 页模式实现。多值 Form 字段签名例：`op_seq: list[str] = Form(default=[])`。）

- [ ] **Step 6: 运行测试 + 回归 + Commit**

Run → PASS。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata src/lightmes/templates/masterdata tests/modules/masterdata/test_routing_bom_pages.py
git commit -m "feat: add routing editor, BOM view page, product source badges"
```

---

### Task 8: 首页导航扩展 + 全量回归

首页主数据卡片区列出全部管理页 + ERP 导入入口；全量回归。

**Files:**
- Modify: `src/lightmes/templates/home.html`
- Test: 全量回归

- [ ] **Step 1: 扩展首页导航**

在 `home.html` 的"主数据"卡片区补齐 nav-card（产线/作业站/工艺路径/BOM/SN规则），并加"ERP 导入"入口（可放主数据区或单列"集成"区）。沿用现有 `.nav-card` 结构 + emoji 图标。示例补充：
```html
    <a class="nav-card" href="/masterdata/lines"><span class="nav-card__icon">🏭</span>
      <div class="nav-card__name">产线管理</div><div class="nav-card__desc">产线布局</div></a>
    <a class="nav-card" href="/masterdata/work-stations"><span class="nav-card__icon">🔧</span>
      <div class="nav-card__name">作业站管理</div><div class="nav-card__desc">工位配置</div></a>
    <a class="nav-card" href="/masterdata/routings"><span class="nav-card__icon">🛠️</span>
      <div class="nav-card__name">工艺路径</div><div class="nav-card__desc">工序编排</div></a>
    <a class="nav-card" href="/masterdata/boms"><span class="nav-card__icon">📋</span>
      <div class="nav-card__name">BOM 管理</div><div class="nav-card__desc">物料清单</div></a>
    <a class="nav-card" href="/production/sn-rules"><span class="nav-card__icon">🔢</span>
      <div class="nav-card__name">SN 规则</div><div class="nav-card__desc">编码规则</div></a>
    <a class="nav-card" href="/integration/import"><span class="nav-card__icon">📥</span>
      <div class="nav-card__name">ERP 导入</div><div class="nav-card__desc">主数据下发</div></a>
```

- [ ] **Step 2: 全量回归 + app 冒烟**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v
```
全绿。冒烟：`uv run python -c "import lightmes.main; print('ok')"`；确认首页可访问、各新页面路由存在。

- [ ] **Step 3: Commit**

```bash
git add src/lightmes/templates/home.html
git commit -m "chore: extend home navigation with all master-data pages and ERP import"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2b spec §3/§4/§5/§6）：
- product/bom/routing 加 source/erp_ref/synced_at + erp_ref 部分唯一索引 → Task 1 ✅
- upsert_product/upsert_bom 同步逻辑（打标/幂等/不覆盖 manual/组件 code 解析）→ Task 2/3 ✅
- integration 模块 + ErpSyncService 抽象 + FileErpSyncService（CSV/JSON、部分成功）→ Task 4 ✅
- 导入 API + 导入页 → Task 5 ✅
- line/work_station/sn_rule 管理页 → Task 6 ✅
- routing 编辑页 + product 来源徽标 + bom 页 → Task 7 ✅
- 首页导航扩展 → Task 8 ✅
- 本期 ERP 导入只 product+bom（routing 本地编辑做、ERP 同步留后）→ Task 4 抽象只声明 sync_products/sync_boms ✅

**占位符扫描**：Task 6 Step 4 与 Task 7 Step 5 的页面路由处理器给了"参照现有 products 页模式实现"的指引而非逐行完整代码——因为它们是把已存在的 products 页 CRUD 模式（current_user_or_none + Form + 片段渲染 + rollback）机械套用到新实体，模板已完整给出。实现时对照 `masterdata/router.py` 现有 `products_page`/`products_create_page` 等价套用。其余步骤含完整代码。这是唯一非逐行处，已明确指出参照物。

**类型一致性**：`source/erp_ref/synced_at`、`ProductUpsert/BomUpsert/BomItemUpsert`、`upsert_product/upsert_bom`(→tuple[obj,str])、`get_by_erp_ref`、`SyncResult`、`ErpSyncService/FileErpSyncService.sync_products/sync_boms`、`list_all`(各 repo)、路由路径 —— 定义处与引用处一致 ✅。

**迁移**：Task 1 加三列+三部分唯一索引，source 带 server_default="manual" 避免现有行 NOT NULL 冲突；打开迁移核对不误删既有索引。
