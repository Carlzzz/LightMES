# P2i 工序级物料校验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"何时该装哪个件"前置到具体工序：每个 BOM 行声明其装配工序（`consume_at_operation_seq`），过站时即时校验"应装未装"，扫错件时即时拦截，最终工序累积校验保留作兜底。

**Architecture:** 单列扩展（`bom_items.consume_at_operation_seq: int | None`，NULL = 兼容老数据 = 仅最终校验）。三层校验：① 即时（当前 op 应装的件）② 扫错件拦截（在 bind_components）③ 最终工序累积兜底（保留现有逻辑）。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (`Mapped[]`), Alembic, Pydantic v2, Jinja2+HTMX, PostgreSQL, pytest, uv

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（必须 127.0.0.1，不用 localhost，否则 Windows IPv6 ~130s 卡顿）
- 测试用 `db_session` fixture（conftest.py 的 SAVEPOINT 隔离），不直接 commit
- 服务层抛 `BusinessRuleError` / `ValidationError` / `NotFoundError` / `ConflictError`（来自 `lightmes.shared.errors`）
- 事件总线：`lightmes.shared.events.event_bus.publish(...)`
- 文案中文，按钮/错误信息含具体工序号/件名
- 不加 DB 层 FK 约束到 `operations.seq`（seq 跨 routing 含义不同，由 app 层基于 Product → active Routing 校验）
- 老数据（NULL seq）行为：仅最终工序累积校验（= 现有行为），零破坏
- 迁移必须可 downgrade

---

### Task 1: Migration + Model + Schemas + Service plumbing

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py` (BomItem 类，约 line 74-86)
- Create: `src/lightmes/migrations/versions/<new>_add_consume_at_operation_seq_to_bom_items.py`
- Modify: `src/lightmes/modules/masterdata/schemas.py` (BomItemCreate, BomItemUpsert, BomItemRead)
- Modify: `src/lightmes/modules/masterdata/service.py` (create_bom 约 247-274, upsert_bom 约 313-341)
- Test: `tests/modules/masterdata/test_upsert_bom.py` (扩展已有测试)

**Interfaces:**
- Consumes: 既有 `MasterDataService.create_bom` / `upsert_bom`
- Produces:
  - `bom_items.consume_at_operation_seq` 列（NULL 兼容）
  - `BomItemCreate.consume_at_operation_seq: int | None = None`
  - `BomItemUpsert.consume_at_operation_seq: int | None = None`
  - `BomItemRead.consume_at_operation_seq: int | None`
  - `MasterDataService.create_bom` / `upsert_bom` 透传该字段

- [ ] **Step 1: 加 model 字段**

修改 `src/lightmes/modules/masterdata/models.py` 的 `BomItem` 类（约 line 74-86），在 `track_mode` 之后追加：

```python
class BomItem(Base, TimestampMixin):
    __tablename__ = "bom_items"
    __table_args__ = (
        UniqueConstraint(
            "bom_id", "component_product_id", name="uq_bom_item_component"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    track_mode: Mapped[str] = mapped_column()  # denormalized from component product
    consume_at_operation_seq: Mapped[int | None] = mapped_column(default=None)
```

- [ ] **Step 2: 生成 Alembic 迁移文件**

创建 `src/lightmes/migrations/versions/a7c3e9f12b4d_add_consume_at_operation_seq_to_bom_items.py`：

```python
"""add_consume_at_operation_seq_to_bom_items

Revision ID: a7c3e9f12b4d
Revises: 49ca97c7b192
Create Date: 2026-08-11 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c3e9f12b4d'
down_revision = '49ca97c7b192'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bom_items",
        sa.Column("consume_at_operation_seq", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bom_items", "consume_at_operation_seq")
```

- [ ] **Step 3: 应用迁移验证**

Run: `uv run alembic upgrade head`
Expected: 输出 `Running upgrade 49ca97c7b192 -> a7c3e9f12b4d, add_consume_at_operation_seq_to_bom_items`，无报错。

Run: `uv run alembic downgrade -1`
Expected: 输出 `Running downgrade a7c3e9f12b4d -> 49ca97c7b192`，无报错。

Run: `uv run alembic upgrade head`（再升回去）

- [ ] **Step 4: 扩展 schemas**

修改 `src/lightmes/modules/masterdata/schemas.py`：

`BomItemCreate`（约 line 83-85）：
```python
class BomItemCreate(BaseModel):
    component_product_id: int
    qty: float = 1
    consume_at_operation_seq: int | None = None
```

`BomCreate`（约 line 88-91）保持不变。

`BomItemUpsert`（约 line 94-96）：
```python
class BomItemUpsert(BaseModel):
    component_code: str
    qty: float = 1
    consume_at_operation_seq: int | None = None
```

`BomItemRead`（约 line 105-110）：
```python
class BomItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    component_product_id: int
    qty: float
    track_mode: str
    consume_at_operation_seq: int | None
```

- [ ] **Step 5: 服务层透传**

修改 `src/lightmes/modules/masterdata/service.py` 的 `create_bom`（约 line 266-272）：

```python
        for item in data.items:
            self.db.add(BomItem(
                bom_id=bom.id,
                component_product_id=item.component_product_id,
                qty=item.qty,
                track_mode=components[item.component_product_id].track_mode,
                consume_at_operation_seq=item.consume_at_operation_seq,
            ))
```

修改 `upsert_bom`（约 line 327-340），existing 分支和新建分支都加：

```python
        existing = self.boms.get_by_erp_ref(data.erp_ref)
        if existing is not None:
            if product.id != existing.product_id:
                raise ValueError(f"BOM {data.erp_ref} 的成品与已存在记录不一致")
            self.boms.delete_items(existing.id)
            for comp, qty in resolved:
                self.db.add(BomItem(bom_id=existing.id,
                    component_product_id=comp.id, qty=qty, track_mode=comp.track_mode))
            existing.synced_at = datetime.now(timezone.utc)
            self.db.flush()
            return existing, "updated"
```

注意：upsert_bom 走 ERP 同步路径，本期 ERP 不传 consume_at_operation_seq，所以该路径默认 None。无需修改 upsert_bom。

但 `create_bom` 已加 consume_at_operation_seq 透传，足够覆盖 BOM 编辑器路径。

- [ ] **Step 6: 加 schema plumbing 测试**

在 `tests/modules/masterdata/test_upsert_bom.py` 末尾追加：

```python
def test_create_bom_persists_consume_at_operation_seq(db_session):
    """create_bom 透传 consume_at_operation_seq 到 BomItem。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN2", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1B", name="件", type="component", track_mode="serial"))
    bom = md.create_bom(BomCreate(product_id=md.products.get_by_code("FIN2").id, items=[
        BomItemCreate(component_product_id=md.products.get_by_code("C1B").id, qty=1,
                      consume_at_operation_seq=3),
    ]))
    items = md.boms.items_of(bom.id)
    assert items[0].consume_at_operation_seq == 3


def test_create_bom_consume_op_defaults_none(db_session):
    """不传 consume_at_operation_seq 时默认 None（兼容老行为）。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    md.create_product(ProductCreate(code="FIN3", name="成品", type="finished"))
    md.create_product(ProductCreate(code="C1C", name="件", type="component", track_mode="serial"))
    bom = md.create_bom(BomCreate(product_id=md.products.get_by_code("FIN3").id, items=[
        BomItemCreate(component_product_id=md.products.get_by_code("C1C").id, qty=1),
    ]))
    items = md.boms.items_of(bom.id)
    assert items[0].consume_at_operation_seq is None
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/masterdata/test_upsert_bom.py -v`
Expected: 所有测试 PASS（包括新加 2 个 + 已有测试无回归）。

- [ ] **Step 8: 运行 BOM 相关全套回归**

Run: `uv run pytest tests/modules/masterdata/ -v -k "bom or routing_bom"`
Expected: 全部 PASS。

- [ ] **Step 9: Commit**

```bash
git add src/lightmes/modules/masterdata/models.py \
        src/lightmes/modules/masterdata/schemas.py \
        src/lightmes/modules/masterdata/service.py \
        src/lightmes/migrations/versions/a7c3e9f12b4d_add_consume_at_operation_seq_to_bom_items.py \
        tests/modules/masterdata/test_upsert_bom.py
git commit -m "feat(bom): add consume_at_operation_seq column + schema plumbing"
```

---

### Task 2: MasterDataQueryService.get_bom_items_by_consume_op

**Files:**
- Modify: `src/lightmes/modules/masterdata/query_service.py` (追加方法)
- Test: `tests/modules/masterdata/test_query_service_bom.py`

**Interfaces:**
- Consumes: Task 1 的 `BomItem.consume_at_operation_seq` 字段
- Produces: `MasterDataQueryService.get_bom_items_by_consume_op(product_id: int, op_seq: int) -> list[BomItem]`

- [ ] **Step 1: 写失败测试**

在 `tests/modules/masterdata/test_query_service_bom.py` 末尾追加（若文件存在；否则创建）：

```python
def test_get_bom_items_by_consume_op_returns_matching_items(db_session):
    """get_bom_items_by_consume_op 返回 consume_at_operation_seq == op_seq 的 active BOM 行。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    from lightmes.modules.masterdata.query_service import MasterDataQueryService

    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="QF1", name="成品", type="finished"))
    c1 = md.create_product(ProductCreate(code="QC1", name="件1", type="component", track_mode="serial"))
    c2 = md.create_product(ProductCreate(code="QC2", name="件2", type="component", track_mode="serial"))
    c3 = md.create_product(ProductCreate(code="QC3", name="件3", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1, consume_at_operation_seq=2),
        BomItemCreate(component_product_id=c2.id, qty=1, consume_at_operation_seq=3),
        BomItemCreate(component_product_id=c3.id, qty=1),  # NULL = 兼容老行为
    ]))

    svc = MasterDataQueryService(db_session)
    op2_items = svc.get_bom_items_by_consume_op(fin.id, 2)
    op3_items = svc.get_bom_items_by_consume_op(fin.id, 3)
    op4_items = svc.get_bom_items_by_consume_op(fin.id, 4)

    assert {i.component_product_id for i in op2_items} == {c1.id}
    assert {i.component_product_id for i in op3_items} == {c2.id}
    assert op4_items == []  # 不返回 NULL 的项


def test_get_bom_items_by_consume_op_returns_empty_when_no_active_bom(db_session):
    """无 active BOM 时返回空列表。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import ProductCreate
    from lightmes.modules.masterdata.query_service import MasterDataQueryService

    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="QF2", name="成品", type="finished"))

    svc = MasterDataQueryService(db_session)
    assert svc.get_bom_items_by_consume_op(fin.id, 1) == []
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/masterdata/test_query_service_bom.py -v`
Expected: FAIL，`AttributeError: 'MasterDataQueryService' object has no attribute 'get_bom_items_by_consume_op'`

- [ ] **Step 3: 实现方法**

修改 `src/lightmes/modules/masterdata/query_service.py`，在 `get_active_bom_items` 方法后追加：

```python
    def get_bom_items_by_consume_op(
        self, product_id: int, op_seq: int,
    ) -> list[BomItem]:
        """返回 consume_at_operation_seq == op_seq 的 active BOM 行。

        NULL consume_at_operation_seq 不返回（兼容老数据，仅最终工序累积校验参与）。
        """
        bom = self._boms.get_active_by_product(product_id)
        if bom is None:
            return []
        return [i for i in self._boms.items_of(bom.id)
                if i.consume_at_operation_seq == op_seq]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/modules/masterdata/test_query_service_bom.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/masterdata/query_service.py \
        tests/modules/masterdata/test_query_service_bom.py
git commit -m "feat(masterdata): get_bom_items_by_consume_op query method"
```

---

### Task 3: GenealogyService.bind_components 增加 current_op_seq 参数

**Files:**
- Modify: `src/lightmes/modules/trace/genealogy_service.py` (bind_components 签名 + 新校验)
- Modify: `src/lightmes/modules/production/operation_pass_service.py` (调用点传入 expected.seq，约 line 194-204)
- Test: `tests/modules/trace/test_genealogy_service.py`

**Interfaces:**
- Consumes: Task 1 的 `BomItem.consume_at_operation_seq`
- Produces: `GenealogyService.bind_components(...)` 增加 `current_op_seq: int | None = None` 形参，触发扫错件拦截

- [ ] **Step 1: 写失败测试**

在 `tests/modules/trace/test_genealogy_service.py` 末尾追加：

```python
def test_bind_blocks_when_component_belongs_to_later_op(db_session):
    """扫的件 BOM 行声明 consume_at_operation_seq=3，但当前 op_seq=2 → 拦截。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BLOPF", name="成品", type="finished"))
    c_late = md.create_product(
        ProductCreate(code="BLC1", name="后装件", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_late.id, qty=1,
                      consume_at_operation_seq=3),
    ]))
    line = md.create_line(LineCreate(code="BLL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="BLW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="BLR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=i, code=f"OP{i}", name=f"工序{i}",
                                    default_work_station_id=w.id,
                                    allowed_work_station_ids=[w.id])
                    for i in range(1, 4)]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="BLWO", product_id=fin.id, routing_id=r.id,
                        line_id=line.id, qty=10))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="BL1", work_order_id=wo.id, product_id=fin.id))

    svc = GenealogyService(db_session)
    with pytest.raises(BusinessRuleError) as exc:
        svc.bind_components(su, [
            ComponentBind(component_product_id=c_late.id, component_sn="X-1"),
        ], operator_id=None, current_op_seq=2)
    assert "工序 3" in str(exc.value)


def test_bind_blocks_when_component_belongs_to_earlier_op(db_session):
    """扫的件 BOM 行声明 consume_at_operation_seq=1，但当前 op_seq=3 → 拦截（防回补）。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BLOEF", name="成品", type="finished"))
    c_early = md.create_product(
        ProductCreate(code="BEC1", name="早装件", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_early.id, qty=1,
                      consume_at_operation_seq=1),
    ]))
    line = md.create_line(LineCreate(code="BEL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="BEW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="BER", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=i, code=f"OP{i}", name=f"工序{i}",
                                    default_work_station_id=w.id,
                                    allowed_work_station_ids=[w.id])
                    for i in range(1, 4)]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="BEWO", product_id=fin.id, routing_id=r.id,
                        line_id=line.id, qty=10))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="BE1", work_order_id=wo.id, product_id=fin.id))

    svc = GenealogyService(db_session)
    with pytest.raises(BusinessRuleError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=c_early.id, component_sn="Y-1"),
        ], operator_id=None, current_op_seq=3)


def test_bind_allows_when_consume_op_matches_current_op(db_session):
    """扫的件 BOM 行声明 consume_at_operation_seq=2，当前 op_seq=2 → 通过。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, BomCreate, BomItemCreate,
    )
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="BLOKF", name="成品", type="finished"))
    c_match = md.create_product(
        ProductCreate(code="BKC1", name="匹配件", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_match.id, qty=1,
                      consume_at_operation_seq=2),
    ]))
    line = md.create_line(LineCreate(code="BKL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="BKW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="BKR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=i, code=f"OP{i}", name=f"工序{i}",
                                    default_work_station_id=w.id,
                                    allowed_work_station_ids=[w.id])
                    for i in range(1, 4)]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="BKWO", product_id=fin.id, routing_id=r.id,
                        line_id=line.id, qty=10))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="BK1", work_order_id=wo.id, product_id=fin.id))

    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_match.id, component_sn="Z-1"),
    ], operator_id=None, current_op_seq=2)
    assert len(binds) == 1


def test_bind_allows_when_consume_op_is_null(db_session):
    """扫的件 BOM 行 consume_at_operation_seq = NULL（老数据）→ 任何 op 都放行。"""
    # 使用文件顶部的 _setup()：c_ser 的 BOM 行没有 consume_at_operation_seq
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("NULL1")
    svc = GenealogyService(db_session)
    # current_op_seq=99 仍应通过（NULL 兼容）
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_ser.id, component_sn="N-1"),
    ], operator_id=None, current_op_seq=99)
    assert len(binds) == 1


def test_bind_skips_op_check_when_current_op_seq_none(db_session):
    """current_op_seq = None（向后兼容现有调用方）→ 跳过扫错件校验。"""
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("NONE1")
    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_ser.id, component_sn="M-1"),
    ], operator_id=None, current_op_seq=None)
    assert len(binds) == 1
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/trace/test_genealogy_service.py -v -k "blocks_or_matches_or_null_or_skips"`
Expected: FAIL，5 个新测试因 `bind_components() got an unexpected keyword argument 'current_op_seq'` 失败。

- [ ] **Step 3: 修改 bind_components 签名 + 加扫错件校验**

修改 `src/lightmes/modules/trace/genealogy_service.py` 的 `bind_components` 方法：

```python
    def bind_components(
        self, parent_su, components: list[ComponentBind],
        operator_id: int | None,
        operation_record_id: int | None = None,
        current_op_seq: int | None = None,
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
            # 新增：扫错件拦截（current_op_seq 非 None 时校验）
            if (current_op_seq is not None
                    and item.consume_at_operation_seq is not None
                    and item.consume_at_operation_seq != current_op_seq):
                raise BusinessRuleError(
                    f"此物料应在工序 {item.consume_at_operation_seq} 装配，"
                    f"不可在工序 {current_op_seq} 扫描")
            bind = self.binds.add(GenealogyBind(
                parent_sn_id=parent_su.id,
                component_product_id=comp.component_product_id,
                component_type=track,
                component_sn=comp.component_sn,
                component_batch_no=comp.component_batch_no,
                qty=comp.qty,
                operator_id=operator_id,
                operation_record_id=operation_record_id,
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
```

- [ ] **Step 4: 修改 operation_pass_service 调用点传入 expected.seq**

修改 `src/lightmes/modules/production/operation_pass_service.py` 约 line 194-204 的 `bind_components` 调用：

```python
                binds = GenealogyService(self.db).bind_components(
                    su,
                    [ComponentBind(
                        component_product_id=c.component_product_id,
                        component_sn=c.component_sn,
                        component_batch_no=c.component_batch_no,
                        qty=c.qty,
                    ) for c in data.components],
                    operator_id=data.operator_id,
                    operation_record_id=record.id,
                    current_op_seq=expected.seq,
                )
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/trace/test_genealogy_service.py -v`
Expected: 所有测试 PASS（新加 5 个 + 已有测试无回归）。

- [ ] **Step 6: 运行 operation_pass 回归**

Run: `uv run pytest tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skip.py tests/modules/production/test_operation_pass_rework_station.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/trace/genealogy_service.py \
        src/lightmes/modules/production/operation_pass_service.py \
        tests/modules/trace/test_genealogy_service.py
git commit -m "feat(genealogy): block components bound at wrong operation (consume_at_operation_seq)"
```

---

### Task 4: operation_pass_service 5d 改造（即时校验）

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py` (5d 块，约 line 137-168)
- Test: `tests/modules/production/test_operation_pass.py` (扩展)

**Interfaces:**
- Consumes: Task 2 的 `get_bom_items_by_consume_op`，Task 3 的 `bind_components(current_op_seq=...)`
- Produces: 过站时即时校验"本工序应装未装"

- [ ] **Step 1: 写失败测试**

在 `tests/modules/production/test_operation_pass.py` 末尾追加：

```python
def _line_with_op_bom(db_session, n_ops=3):
    """构造带 consume_at_operation_seq 的 BOM 测试环境。

    c_op2 声明在 op2 装配，c_op3 声明在 op3 装配。
    """
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    p, line, ws, wo = _line(db_session, n_ops=n_ops)
    md = MasterDataService(db_session)
    c_op2 = md.create_product(ProductCreate(code="COP2", name="op2件",
                                            type="component", track_mode="serial"))
    c_op3 = md.create_product(ProductCreate(code="COP3", name="op3件",
                                            type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=p.id, items=[
        BomItemCreate(component_product_id=c_op2.id, qty=1,
                      consume_at_operation_seq=2),
        BomItemCreate(component_product_id=c_op3.id, qty=1,
                      consume_at_operation_seq=3),
    ]))
    return p, line, ws, wo, c_op2, c_op3


def test_pass_blocks_when_required_part_for_op_not_scanned(db_session):
    """op2 应装 c_op2 但未扫 → 即时校验拦截。"""
    from lightmes.modules.production.schemas import OperationPassInput
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    # 首检不涉及，op1 无应装件，过 op1
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    # op2 应装 c_op2 但未扫
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(OperationPassInput(
            work_station_id=ws[1].id, sn=r1.sn))
    assert "op2件" in str(exc.value)


def test_pass_ok_when_required_part_scanned_this_op(db_session):
    """op2 应装 c_op2，扫了 → 通过。"""
    from lightmes.modules.production.schemas import (
        OperationPassInput, ComponentInput,
    )
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    r2 = svc.pass_operation(OperationPassInput(
        work_station_id=ws[1].id, sn=r1.sn,
        components=[ComponentInput(
            component_product_id=c_op2.id, component_sn="SN-OP2-1",
            component_batch=None, qty=1)]))
    assert r2.passed_op.seq == 2


def test_pass_blocks_when_scanning_part_for_future_op(db_session):
    """op2 扫了 op3 的件 → 扫错件拦截（在 bind_components）。"""
    from lightmes.modules.production.schemas import (
        OperationPassInput, ComponentInput,
    )
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(OperationPassInput(
            work_station_id=ws[1].id, sn=r1.sn,
            components=[ComponentInput(
                component_product_id=c_op3.id, component_sn="SN-OP3-early",
                component_batch=None, qty=1)]))
    assert "工序 3" in str(exc.value)


def test_final_op_cumulative_check_still_blocks_missing(db_session):
    """op3 时 c_op2 未装（应在前序但漏了）→ 最终累积兜底拦截。"""
    from lightmes.modules.production.schemas import OperationPassInput, ComponentInput
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    # op2 不扫 c_op2，绕过即时校验？不可能 —— 即时校验会拦。
    # 所以本测试用 NULL BOM 模拟"漏检到最终"场景：
    # 改用 NULL consume_at_operation_seq 的 BOM
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    p2, line2, ws2, wo2 = _line(db_session, n_ops=2)
    c_null = MasterDataService(db_session).create_product(
        ProductCreate(code="CNULL", name="老件", type="component", track_mode="serial"))
    MasterDataService(db_session).create_bom(BomCreate(product_id=p2.id, items=[
        BomItemCreate(component_product_id=c_null.id, qty=1)]))  # NULL seq
    r_a = svc.pass_operation(OperationPassInput(work_station_id=ws2[0].id,
                                                 work_order_code=wo2.code))
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(OperationPassInput(work_station_id=ws2[1].id, sn=r_a.sn))
    assert "老件" in str(exc.value)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_operation_pass.py -v -k "required_part or scanning_part or cumulative_check"`
Expected: 部分测试 FAIL（即时校验未实现时 `test_pass_blocks_when_required_part_for_op_not_scanned` 会失败；扫错件测试如果 Task 3 已合并应已通过）。

- [ ] **Step 3: 实现 5d 即时校验**

修改 `src/lightmes/modules/production/operation_pass_service.py` 约 line 137-168 的 5d 块，替换为：

```python
        # 5d. 物料绑定校验
        # 5d-① 即时校验：本工序应装的件（consume_at_operation_seq == expected.seq）
        op_bom_items = self.query.get_bom_items_by_consume_op(
            wo.product_id, expected.seq)
        if op_bom_items:
            from collections import Counter
            from lightmes.modules.trace.repository import GenealogyBindRepository
            existing_binds = GenealogyBindRepository(self.db).list_active_by_parent(su.id)
            provided_counts: Counter[int] = Counter()
            for b in existing_binds:
                provided_counts[b.component_product_id] += 1
            for c in data.components:
                provided_counts[c.component_product_id] += 1
            missing = []
            for item in op_bom_items:
                if item.track_mode == "none":
                    continue
                comp = self.query.get_product(item.component_product_id)
                comp_name = comp.name if comp else f"#{item.component_product_id}"
                provided = provided_counts.get(item.component_product_id, 0)
                required = int(item.qty) if item.track_mode == "serial" else 1
                if provided == 0:
                    missing.append(f"{comp_name}（{item.track_mode}）")
                elif item.track_mode == "serial" and provided < required:
                    missing.append(
                        f"{comp_name}（serial，需 {required} 件，已绑 {provided} 件）")
            if missing:
                raise BusinessRuleError(
                    f"物料绑定不完整，不可过站：{', '.join(missing)}")

        # 5d-③ 最终工序累积兜底（保留现有逻辑）
        bom_items = self.query.get_active_bom_items(wo.product_id)
        is_last_op = False
        if bom_items:
            is_last_op = (expected.id == operations[-1].id) if operations else False
            if is_last_op:
                from collections import Counter
                from lightmes.modules.trace.repository import GenealogyBindRepository
                # 累积已绑组件
                existing_binds = GenealogyBindRepository(self.db).list_active_by_parent(su.id)
                provided_counts: Counter[int] = Counter()
                for b in existing_binds:
                    provided_counts[b.component_product_id] += 1
                for c in data.components:
                    provided_counts[c.component_product_id] += 1
                missing = []
                for item in bom_items:
                    if item.track_mode == "none":
                        continue
                    comp = self.query.get_product(item.component_product_id)
                    comp_name = comp.name if comp else f"#{item.component_product_id}"
                    provided = provided_counts.get(item.component_product_id, 0)
                    required = int(item.qty) if item.track_mode == "serial" else 1
                    if provided == 0:
                        missing.append(f"{comp_name}（{item.track_mode}）")
                    elif item.track_mode == "serial" and provided < required:
                        missing.append(
                            f"{comp_name}（serial，需 {required} 件，已绑 {provided} 件）")
                if missing:
                    raise BusinessRuleError(
                        f"物料绑定不完整，不可过站：{', '.join(missing)}")
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/production/test_operation_pass.py -v`
Expected: 所有测试 PASS（新加 4 个 + 已有测试无回归）。

- [ ] **Step 5: 运行 operation_pass 全套回归**

Run: `uv run pytest tests/modules/production/ -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/production/operation_pass_service.py \
        tests/modules/production/test_operation_pass.py
git commit -m "feat(production): per-operation material check (immediate + final cumulative)"
```

---

### Task 5: BOM 编辑器 UI（消耗工序下拉）

**Files:**
- Modify: `src/lightmes/templates/masterdata/boms.html` (扩展显示)
- Create: `src/lightmes/templates/masterdata/bom_detail.html` (BOM 详情/编辑页)
- Modify: `src/lightmes/modules/masterdata/api_router.py` (新增 GET /boms/{id}/edit-data 返回 op 选项)
- Test: `tests/modules/masterdata/test_routing_bom_pages.py` (扩展)

**Interfaces:**
- Consumes: Task 1 的 `BomItemRead.consume_at_operation_seq`
- Produces: BOM 详情页可编辑每个 BOM 行的 consume_at_operation_seq

**说明：** 当前 BOM 编辑通过 ERP 同步或 `create_bom` API；本期为编辑器加"消耗工序"下拉列。BOM 详情页（新建）显示该 Product active Routing 的 op 列表供选择。

- [ ] **Step 1: 加 PATCH 端点更新单行 consume_at_operation_seq**

`api_router.py` 已 import `require_role`、`User`、`Operation`、`MasterDataService`、`MasterDataQueryService`。需追加 import `BomItem`、`Bom`、`Routing` 到现有 masterdata models import（具体路径看文件 line 8 附近的 `from lightmes.modules.masterdata.models import Operation`）。

修改 `src/lightmes/modules/masterdata/api_router.py`，在现有 `get_bom` 路由后追加：

```python
from pydantic import BaseModel


class BomItemConsumeOpUpdate(BaseModel):
    consume_at_operation_seq: int | None


@router.patch("/bom-items/{item_id}/consume-op")
def update_bom_item_consume_op(
    item_id: int,
    data: BomItemConsumeOpUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> dict:
    """更新单行 BOM 的 consume_at_operation_seq（仅 admin/supervisor）。

    若该 BOM 的 Product 有 active Routing，seq 必须属于该 Routing 的某个 op。
    """
    item = db.get(BomItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"BOM 行不存在: {item_id}")
    if data.consume_at_operation_seq is not None:
        bom = db.get(Bom, item.bom_id)
        routing = db.query(Routing).filter(
            Routing.product_id == bom.product_id, Routing.status == "active"
        ).first()
        if routing is None:
            raise HTTPException(
                status_code=400, detail="该成品无 active Routing，无法指定消耗工序")
        valid_seqs = {op.seq for op in db.query(Operation).filter(
            Operation.routing_id == routing.id).all()}
        if data.consume_at_operation_seq not in valid_seqs:
            raise HTTPException(
                status_code=400,
                detail=f"工序 seq {data.consume_at_operation_seq} 不属于该成品 active Routing")
    item.consume_at_operation_seq = data.consume_at_operation_seq
    db.flush()
    db.commit()
    return {"ok": True, "item_id": item_id,
            "consume_at_operation_seq": item.consume_at_operation_seq}
```

import 部分需在 line 8 修改为（确认实际行号）：

```python
from lightmes.modules.masterdata.models import Bom, BomItem, Operation, Routing
```

- [ ] **Step 2: 写 API 测试**

`tests/modules/masterdata/test_routing_bom_pages.py` 已有 `client` fixture + `_login` helper（直接复用）。在该文件顶部 import 追加：

```python
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
```

在文件末尾追加（admin 登录用 `role="admin"` 直写，满足 `require_role("admin","supervisor")` 的 legacy 兼容路径）：

```python
def _login_admin(client, db_session):
    """登录一个 admin 用户（满足 require_role("admin","supervisor")）。"""
    from lightmes.modules.auth.service import AuthService
    from lightmes.modules.auth.schemas import UserCreate
    AuthService(db_session).create_user(
        UserCreate(username="admbom", password="pw12345", display_name="Adm"))
    # 直接 SQL 改 legacy role 字段（role_obj 创建留给系统初始化）
    from lightmes.modules.auth.models import User as U
    u = db_session.query(U).filter(U.username == "admbom").one()
    u.role = "admin"
    db_session.flush()
    client.post("/login", data={"username": "admbom", "password": "pw12345"})


def _bom_for_patch(db_session):
    """构造 product + active routing (3 ops) + active BOM (1 item)。返回 (bom, item, op_seqs)。"""
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="PBF", name="成品", type="finished"))
    c1 = md.create_product(ProductCreate(code="PBC", name="件", type="component", track_mode="serial"))
    line = md.create_line(LineCreate(code="PBL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="PBW", name="站", line_id=line.id, seq=1))
    md.create_routing(RoutingCreate(
        code="PBR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=i, code=f"OP{i}", name=f"工序{i}",
                                    default_work_station_id=w.id,
                                    allowed_work_station_ids=[w.id])
                    for i in range(1, 4)]))
    bom = md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c1.id, qty=1)]))
    items = md.boms.items_of(bom.id)
    return bom, items[0], [1, 2, 3]


def test_patch_bom_item_consume_op_updates_field(db_session, client):
    """PATCH /api/bom-items/{id}/consume-op 更新 consume_at_operation_seq 成功。"""
    bom, item, _ = _bom_for_patch(db_session)
    db_session.flush()
    _login_admin(client, db_session)
    resp = client.patch(f"/api/bom-items/{item.id}/consume-op",
                        json={"consume_at_operation_seq": 2})
    assert resp.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(type(item), item.id)
    assert refreshed.consume_at_operation_seq == 2


def test_patch_bom_item_consume_op_rejects_invalid_seq(db_session, client):
    """PATCH 用不属于 routing 的 seq → 400。"""
    bom, item, _ = _bom_for_patch(db_session)
    db_session.flush()
    _login_admin(client, db_session)
    resp = client.patch(f"/api/bom-items/{item.id}/consume-op",
                        json={"consume_at_operation_seq": 99})
    assert resp.status_code == 400
    assert "不属于" in resp.text or "Routing" in resp.text


def test_patch_bom_item_consume_op_clears_with_null(db_session, client):
    """PATCH 用 null 清空 consume_at_operation_seq（回退到兼容老行为）。"""
    bom, item, _ = _bom_for_patch(db_session)
    item.consume_at_operation_seq = 2  # 预置
    db_session.flush()
    _login_admin(client, db_session)
    resp = client.patch(f"/api/bom-items/{item.id}/consume-op",
                        json={"consume_at_operation_seq": None})
    assert resp.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(type(item), item.id)
    assert refreshed.consume_at_operation_seq is None

- [ ] **Step 3: 创建 BOM 详情模板**

创建 `src/lightmes/templates/masterdata/bom_detail.html`：

```html
{% extends "base.html" %}
{% block title %}BOM 详情 #{{ bom.id }}{% endblock %}
{% block content %}
<h1 class="page-title">BOM 详情 <small>#{{ bom.id }}</small></h1>
<div class="card">
  <div class="card__title">
    {{ bom.product_code }} {{ bom.product_name }} — 版本 {{ bom.version }}
    <span class="badge">{{ bom.status }}</span>
    {% if bom.source == "erp" %}<span class="badge">ERP</span>{% else %}<span class="badge">本地</span>{% endif %}
  </div>
  <table class="data-table" id="bom-items-table">
    <thead><tr><th>组件</th><th>track_mode</th><th>qty</th><th>消耗工序</th><th></th></tr></thead>
    <tbody>
      {% for item in bom.items %}
      <tr data-item-id="{{ item.id }}">
        <td>{{ item.component_code }} {{ item.component_name }}</td>
        <td>{{ item.track_mode }}</td>
        <td>{{ item.qty }}</td>
        <td>
          <select name="consume_at_operation_seq"
                  onchange="updateConsumeOp({{ item.id }}, this.value)">
            <option value="">仅最终校验（老行为）</option>
            {% for op in operations %}
            <option value="{{ op.seq }}"
                    {% if item.consume_at_operation_seq == op.seq %}selected{% endif %}>
              工序 {{ op.seq }} — {{ op.code }} {{ op.name }}
            </option>
            {% endfor %}
          </select>
        </td>
        <td><span class="badge update-status" style="display:none"></span></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
<p><a href="/masterdata/boms">返回 BOM 列表</a></p>
<script>
function updateConsumeOp(itemId, seqStr) {
  var seq = seqStr ? parseInt(seqStr, 10) : null;
  var row = document.querySelector('tr[data-item-id="' + itemId + '"]');
  var status = row.querySelector('.update-status');
  status.style.display = 'inline-block';
  status.textContent = '保存中...';
  fetch('/api/bom-items/' + itemId + '/consume-op', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({consume_at_operation_seq: seq}),
  }).then(function(r) {
    if (r.ok) {
      status.textContent = '已保存';
      setTimeout(function() { status.style.display = 'none'; }, 1500);
    } else {
      return r.json().then(function(d) {
        status.textContent = '失败：' + (d.detail || r.status);
        alert('更新失败：' + (d.detail || r.status) + '\n请刷新页面');
      });
    }
  }).catch(function(e) {
    status.textContent = '网络错误';
    alert('网络错误：' + e);
  });
}
</script>
{% endblock %}
```

- [ ] **Step 4: 加 page 路由渲染 bom_detail**

在 `src/lightmes/modules/masterdata/page_router.py`（如不存在则在 `api_router.py` 顶部加 HTML 路由）追加：

```python
@router.get("/masterdata/boms/{bom_id}", response_class=HTMLResponse)
def bom_detail_page(
    bom_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> HTMLResponse:
    svc = MasterDataService(db)
    query = MasterDataQueryService(db)
    bom = svc.boms.get(bom_id)
    if bom is None:
        raise HTTPException(404, f"BOM 不存在: {bom_id}")
    items = svc.boms.items_of(bom.id)
    product = query.get_product(bom.product_id)
    # active routing
    routing = db.query(Routing).filter(
        Routing.product_id == bom.product_id, Routing.status == "active"
    ).first()
    operations = []
    if routing is not None:
        operations = query.get_operations(routing.id)
    # 渲染（带 component code/name 拼装）
    item_views = []
    for it in items:
        comp = query.get_product(it.component_product_id)
        item_views.append({
            "id": it.id,
            "component_code": comp.code if comp else str(it.component_product_id),
            "component_name": comp.name if comp else "",
            "track_mode": it.track_mode,
            "qty": float(it.qty),
            "consume_at_operation_seq": it.consume_at_operation_seq,
        })
    return templates.TemplateResponse("masterdata/bom_detail.html", {
        "request": request,
        "bom": {
            "id": bom.id, "version": bom.version, "status": bom.status,
            "source": bom.source, "product_code": product.code if product else "",
            "product_name": product.name if product else "",
            "items": item_views,
        },
        "operations": [{"seq": o.seq, "code": o.code, "name": o.name} for o in operations],
    })
```

- [ ] **Step 5: 在 boms.html 列表加链接**

修改 `src/lightmes/templates/masterdata/boms.html`，把第一列 ID 改为链接：

```html
<tr>
  <td><a href="/masterdata/boms/{{ b.id }}">#{{ b.id }}</a></td>
  <td>...</td>
  ...
</tr>
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/modules/masterdata/ -v`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/templates/masterdata/boms.html \
        src/lightmes/templates/masterdata/bom_detail.html \
        src/lightmes/modules/masterdata/api_router.py \
        src/lightmes/modules/masterdata/page_router.py \
        tests/modules/masterdata/test_routing_bom_pages.py
git commit -m "feat(bom): BOM detail editor with consume_at_operation_seq dropdown"
```

---

### Task 6: 过站页物料过滤 + 全套回归 + memory 更新

**Files:**
- Modify: `src/lightmes/modules/production/station_service.py` (filter components by current op，约 line 129-136)
- Modify: `src/lightmes/templates/production/station_view.html` (添加 "本工序" badge)
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_p2_shopfloor.md`
- Test: `tests/modules/production/test_operation_pass.py` (扩展)

**Interfaces:**
- Consumes: Task 2 的 `get_bom_items_by_consume_op`
- Produces: 过站页只显示本工序应装件 + NULL 兼容件；非本工序的件不显示

- [ ] **Step 1: 写失败测试 - station_service 过滤**

`StationService.load(scan, work_station_id, operator_id)` 是 `src/lightmes/modules/production/station_service.py:35` 的实际签名。

在 `tests/modules/production/test_operation_pass.py` 末尾追加：

```python
def test_station_service_filters_components_to_current_op(db_session):
    """station_view 只显示 consume_at_operation_seq IS NULL OR == 当前 op 的件。"""
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    from lightmes.modules.production.station_service import StationService

    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    # 加一个 NULL seq 的件
    md = MasterDataService(db_session)
    c_null = md.create_product(ProductCreate(code="CNUL", name="老件",
                                             type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=p.id, items=[
        BomItemCreate(component_product_id=c_null.id, qty=1)]))

    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))

    # 进入 op2，应看到 c_op2 + c_null，不应看到 c_op3
    view = StationService(db_session).load(
        scan=r1.sn, work_station_id=ws[1].id, operator_id=None)
    comp_ids = {c.component_product_id for c in view.components}
    assert c_op2.id in comp_ids
    assert c_null.id in comp_ids
    assert c_op3.id not in comp_ids

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/production/test_operation_pass.py::test_station_service_filters_components_to_current_op -v`
Expected: FAIL（要么 import 错，要么 c_op3 还在显示）。

- [ ] **Step 3: 修改 station_service 过滤逻辑**

修改 `src/lightmes/modules/production/station_service.py` 约 line 129-136 的 components 构造块：

```python
            for item in self.query.get_active_bom_items(product.id):
                # 只显示本工序应装件 + NULL 兼容件
                if (item.consume_at_operation_seq is not None
                        and item.consume_at_operation_seq != expected.seq):
                    continue
                comp = self.query.get_product(item.component_product_id)
                components.append(StationComponentView(
                    component_product_id=item.component_product_id,
                    component_code=comp.code if comp else str(item.component_product_id),
                    component_name=comp.name if comp else "",
                    qty=float(item.qty),
                    track_mode=item.track_mode))
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/production/test_operation_pass.py::test_station_service_filters_components_to_current_op -v`
Expected: PASS。

- [ ] **Step 5: 修改 station_view.html 加 "本工序" 提示**

修改 `src/lightmes/templates/production/station_view.html` 约 line 194 的 card title：

```html
        <div class="card">
          <div class="card__title">
            当前工序物料追溯
            <span class="badge">BOM 匹配</span>
            <span style="font-size:12px;color:#666;margin-left:8px;">
              仅显示本工序（seq={{ view.current_op.seq }}）应装件 + 累积件；其余件按工序提示装配
            </span>
          </div>
```

- [ ] **Step 6: 运行全套 production + masterdata + trace 回归**

Run: `uv run pytest tests/modules/production/ tests/modules/masterdata/ tests/modules/trace/ -v`
Expected: 全部 PASS（pre-existing 失败标注为非本期引入）。

- [ ] **Step 7: 更新 memory**

在 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\project_p2_shopfloor.md` 末尾追加：

```markdown
## P2i 工序级物料校验 (2026-08-11 完成)

- `bom_items.consume_at_operation_seq: int | None`（NULL = 仅最终工序累积校验，兼容老数据）
- 三层校验：① 过站时即时校验本 op 应装件 ② bind_components 扫错件拦截 ③ 最终工序累积兜底
- BOM 详情页 `/masterdata/boms/{id}` 可编辑每行消耗工序（admin/supervisor only，PATCH `/api/bom-items/{id}/consume-op`）
- 过站页只显示本 op 应装件 + NULL 兼容件
```

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/production/station_service.py \
        src/lightmes/templates/production/station_view.html \
        tests/modules/production/test_operation_pass.py \
        C:/Users/zhaocao/.claude/projects/C--Users-zhaocao-Documents-GitHub-LightMES/memory/project_p2_shopfloor.md
git commit -m "feat(station): filter components to current op + P2i memory update"
```

---

## 任务依赖

```
Task 1 (migration + schema plumbing)
  ↓
Task 2 (query method)  ────┐
  ↓                        │
Task 3 (bind current_op_seq) ←─┘
  ↓
Task 4 (operation_pass 5d 即时校验)
  ↓
Task 5 (BOM 编辑器 UI) ← 可与 Task 6 并行
Task 6 (过站页过滤 + memory)
```

Task 5 与 Task 6 互不依赖，但建议顺序执行以避免 UI 模板冲突。

## 全套回归（任意 task 完成后均可运行）

```bash
uv run pytest tests/modules/masterdata/ tests/modules/production/ tests/modules/trace/ -v
uv run alembic upgrade head  # 验证迁移可应用
```
