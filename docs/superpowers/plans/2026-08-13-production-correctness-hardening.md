# Production Correctness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make process snapshots authoritative, make material quantity changes auditable, add reversal paths, and make the local test suite independent of an existing database.

**Architecture:** Add a process accessor over `WorkOrder.process_snapshot`, add a `StockMovement` ledger next to `MaterialLot`, route every material quantity mutation through `MaterialLotService`, and add a session-level test truncation fixture. Audit retention and query stay in shared infrastructure.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, pytest, PostgreSQL.

---

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`
- Service exceptions: `lightmes.shared.errors.DomainError` subclasses.
- State fields use `String + CheckConstraint`, not PostgreSQL enums.
- Admin mutations use `html_role_guard` or `require_role`.
- Migration head before this plan: `74eae97a39cb`.
- Commit after each completed task.

---

### Task 1: Snapshot backfill command

**Files:**

- Create: `src/lightmes/modules/production/backfill_snapshots.py`
- Modify: `scripts/create_admin.py` pattern (new CLI command)
- Test: `tests/modules/production/test_backfill_snapshots.py`

- [ ] **Step 1: Write failing test**

Create `tests/modules/production/test_backfill_snapshots.py`:

```python
from sqlalchemy import select

from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.service import ProductionService
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.backfill_snapshots import backfill_work_order_snapshots


def test_backfill_sets_snapshot_for_active_work_orders(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="BF-P", name="P", type="finished"))
    line = md.create_line(LineCreate(code="BF-L", name="L"))
    ws = md.create_work_station(WorkStationCreate(code="BF-W", name="W", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(
        code="BF-R", name="R", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="OP1",
                                    default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="BF-S", name="r", pattern="BF{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="BF-WO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    wo.process_snapshot = None
    db_session.flush()

    updated = backfill_work_order_snapshots(db_session)

    db_session.refresh(wo)
    assert updated == 1
    assert wo.process_snapshot is not None
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/modules/production/test_backfill_snapshots.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement backfill**

Create `src/lightmes/modules/production/backfill_snapshots.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.process_snapshot import build_process_snapshot


def backfill_work_order_snapshots(db: Session) -> int:
    work_orders = list(
        db.execute(
            select(WorkOrder).where(
                WorkOrder.process_snapshot.is_(None),
                WorkOrder.status.in_(("released", "in_process")),
            )
        ).scalars().all()
    )
    for wo in work_orders:
        wo.process_snapshot = build_process_snapshot(db, wo)
    db.flush()
    return len(work_orders)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/modules/production/test_backfill_snapshots.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/production/backfill_snapshots.py tests/modules/production/test_backfill_snapshots.py
git commit -m "feat(production): add work-order snapshot backfill"
```

---

### Task 2: Shared process accessor

**Files:**

- Modify: `src/lightmes/modules/production/process_snapshot.py`
- Modify: `src/lightmes/modules/production/station_service.py`
- Test: `tests/modules/production/test_process_accessor.py`

- [ ] **Step 1: Write failing test**

Create `tests/modules/production/test_process_accessor.py`:

```python
from lightmes.modules.production.process_snapshot import get_work_order_process


def test_process_accessor_prefers_snapshot(db_session, full_station_setup):
    wo = full_station_setup.work_order
    wo.process_snapshot = {
        "operations": [{
            "id": 999,
            "seq": 1,
            "code": "SNAP-OP",
            "name": "Snapshot Op",
            "default_work_station_id": full_station_setup.work_station_id,
            "allowed_work_station_ids": [full_station_setup.work_station_id],
            "required_skill_id": None,
            "required_level": None,
            "sop_text": None,
            "sop_url": None,
        }],
        "bom_items": [],
    }
    db_session.flush()

    process = get_work_order_process(db_session, wo)

    assert process.operations[0].code == "SNAP-OP"
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/modules/production/test_process_accessor.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement accessor**

In `process_snapshot.py` append:

```python
@dataclass(frozen=True)
class WorkOrderProcess:
    operations: list[SnapshotOperation]
    bom_items: list[SnapshotBomItem]


def get_work_order_process(db: Session, work_order: WorkOrder) -> WorkOrderProcess:
    if has_snapshot(work_order):
        return WorkOrderProcess(snapshot_operations(work_order), snapshot_bom_items(work_order))
    query = MasterDataQueryService(db)
    operations = [
        SnapshotOperation(
            id=op.id,
            seq=op.seq,
            code=op.code,
            name=op.name,
            default_work_station_id=op.default_work_station_id,
            allowed_work_station_ids=[ws.id for ws in query.get_allowed_work_stations(op.id)] or [op.default_work_station_id],
            required_skill_id=op.required_skill_id,
            required_level=op.required_level,
            sop_text=op.sop_text,
            sop_url=op.sop_url,
        )
        for op in query.get_operations(work_order.routing_id)
    ]
    items = query.get_active_bom_items(work_order.product_id)
    bom_items = [
        SnapshotBomItem(
            component_product_id=item.component_product_id,
            component_code="",
            component_name="",
            qty=float(item.qty),
            track_mode=item.track_mode,
            consume_at_operation_seq=item.consume_at_operation_seq,
        )
        for item in items
    ]
    return WorkOrderProcess(operations, bom_items)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/modules/production/test_process_accessor.py -v`
Expected: PASS.

- [ ] **Step 5: Refactor StationService**

In `station_service.py`, replace live operations/BOM reads with:

```python
process = get_work_order_process(self.db, wo)
operations = process.operations
```

Keep `get_product`, `get_work_station`, and line checks live because those identifiers are needed for display.

- [ ] **Step 6: Run station tests**

Run: `uv run pytest tests/modules/production/test_station_pages.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/production/process_snapshot.py src/lightmes/modules/production/station_service.py tests/modules/production/test_process_accessor.py
git commit -m "feat(production): make process accessor snapshot-aware"
```

---

### Task 3: Stock movement ledger

**Files:**

- Modify: `src/lightmes/modules/production/models.py`
- Modify: `src/lightmes/modules/production/material_lot_service.py`
- Test: `tests/modules/inventory/test_stock_movements.py`
- Create migration: `src/lightmes/migrations/versions/<next>_add_stock_movements.py`

- [ ] **Step 1: Add model**

In `production/models.py` add:

```python
class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_lot_id: Mapped[int] = mapped_column(ForeignKey("material_lots.id"), index=True)
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    source_type: Mapped[str | None] = mapped_column(String(50), default=None)
    source_id: Mapped[int | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(default=None)
```

Add check constraint:

```python
CheckConstraint(
    "movement_type IN ('receive', 'release', 'consume', 'return', 'adjustment')",
    name="ck_stock_movements_type",
)
```

- [ ] **Step 2: Generate migration**

Run: `uv run alembic revision -m "add_stock_movements"`

Fill `upgrade()` with table creation and `downgrade()` with drop.

- [ ] **Step 3: Apply migration**

Run: `uv run alembic upgrade head`
Expected: PASS.

- [ ] **Step 4: Route service writes through ledger**

In `MaterialLotService.receive()` add:

```python
self.db.add(StockMovement(
    material_lot_id=lot.id,
    movement_type="receive",
    quantity=quantity,
    source_type="material_lot",
    source_id=lot.id,
))
```

In `consume()` add:

```python
self.db.add(StockMovement(
    material_lot_id=lot.id,
    movement_type="consume",
    quantity=-quantity,
    source_type="batch_material_consumption",
    source_id=record.id,
))
```

Add `return_consumed()`:

```python
def return_consumed(self, *, material_lot_id: int, quantity: float, reason: str) -> None:
    lot = self.db.get(MaterialLot, material_lot_id)
    if lot is None:
        raise NotFoundError(f"物料批次不存在: {material_lot_id}")
    lot.available_quantity = float(lot.available_quantity) + quantity
    lot.quantity = float(lot.quantity) + quantity
    if lot.status == "consumed":
        lot.status = "released"
    self.db.add(StockMovement(
        material_lot_id=lot.id,
        movement_type="return",
        quantity=quantity,
        source_type="manual",
        notes=reason,
    ))
    self.db.flush()
```

- [ ] **Step 5: Add tests**

Create `tests/modules/inventory/test_stock_movements.py`:

```python
from sqlalchemy import select

from lightmes.modules.production.models import StockMovement
from lightmes.modules.production.material_lot_service import MaterialLotService


def test_receive_consume_and_return_write_movements(db_session):
    service = MaterialLotService(db_session)
    lot = service.receive(code="MOVE-1", product_id=1, quantity=10)
    service.release(lot.code)
    service.consume(batch_id=1, operation_record_id=None, product_id=1, lot_code="MOVE-1", quantity=3)
    service.return_consumed(material_lot_id=lot.id, quantity=1, reason="rework")

    rows = list(db_session.execute(select(StockMovement).order_by(StockMovement.id)).scalars())
    assert [r.movement_type for r in rows] == ["receive", "consume", "return"]
    assert sum(r.quantity for r in rows) == 8
```

Note: replace hardcoded `batch_id=1` with a real Batch fixture if FK enforcement is strict.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/modules/inventory/test_stock_movements.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/production/models.py src/lightmes/modules/production/material_lot_service.py src/lightmes/migrations/versions/<new_migration>.py tests/modules/inventory/test_stock_movements.py
git commit -m "feat(inventory): add stock movement ledger"
```

---

### Task 4: Return material on unbind and disposition

**Files:**

- Modify: `src/lightmes/modules/trace/genealogy_service.py`
- Modify: `src/lightmes/modules/production/defect_service.py`
- Test: `tests/modules/trace/test_material_return.py`

- [ ] **Step 1: Write failing test**

Create `tests/modules/trace/test_material_return.py`:

```python
from sqlalchemy import select

from lightmes.modules.production.models import BatchMaterialConsumption, MaterialLot
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.trace.genealogy_service import GenealogyService


def test_unbind_returns_batch_consumption(db_session):
    lot = MaterialLotService(db_session).receive(code="RET-1", product_id=1, quantity=5)
    MaterialLotService(db_session).release(lot.code)
    service = MaterialLotService(db_session)
    service.consume(batch_id=1, operation_record_id=None, product_id=1, lot_code="RET-1", quantity=2)

    GenealogyService(db_session).return_batch_consumption(
        material_lot_id=lot.id,
        quantity=2,
        reason="unbind",
    )

    refreshed = db_session.get(MaterialLot, lot.id)
    assert float(refreshed.available_quantity) == 5
```

The test should use a real Batch fixture in the final implementation.

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/modules/trace/test_material_return.py -v`
Expected: FAIL, method missing.

- [ ] **Step 3: Implement return helper**

In `GenealogyService` add:

```python
def return_batch_consumption(self, *, material_lot_id: int, quantity: float, reason: str) -> None:
    MaterialLotService(self.db).return_consumed(
        material_lot_id=material_lot_id,
        quantity=quantity,
        reason=reason,
    )
```

- [ ] **Step 4: Call from unbind**

In `unbind()`, after marking the bind unbound, inspect component type and call the helper for `batch` binds:

```python
if bind.component_type == "batch":
    self.return_batch_consumption(
        material_lot_id=self._lot_id_for_bind(bind),
        quantity=float(bind.qty or 0),
        reason=reason or "unbind",
    )
```

Implement `_lot_id_for_bind()` using `component_batch_no` and `MaterialLotRepository`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/modules/trace/test_material_return.py tests/modules/trace/test_carrier_unbind_page.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/trace/genealogy_service.py tests/modules/trace/test_material_return.py
git commit -m "feat(trace): return batch material on unbind"
```

---

### Task 5: Audit query and retention

**Files:**

- Create: `src/lightmes/shared/audit_service.py`
- Modify: `src/lightmes/shared/audit.py`
- Test: `tests/shared/test_audit_service.py`

- [ ] **Step 1: Write failing test**

Create `tests/shared/test_audit_service.py`:

```python
from datetime import datetime, timedelta
from sqlalchemy import select

from lightmes.shared.audit import AuditLog
from lightmes.shared.audit_service import AuditService


def test_prune_old_audit_logs(db_session):
    old = AuditLog(entity_type="Product", action="created", created_at=datetime.now() - timedelta(days=400))
    new = AuditLog(entity_type="Product", action="created", created_at=datetime.now())
    db_session.add_all([old, new])
    db_session.flush()

    deleted = AuditService(db_session).prune_old(days=365)

    assert deleted == 1
    assert db_session.get(AuditLog, new.id) is not None
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/shared/test_audit_service.py -v`
Expected: FAIL, module missing.

- [ ] **Step 3: Implement service**

Create `src/lightmes/shared/audit_service.py`:

```python
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from lightmes.shared.audit import AuditLog


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def prune_old(self, *, days: int) -> int:
        cutoff = datetime.now() - timedelta(days=days)
        result = self.db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        self.db.flush()
        return result.rowcount or 0
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/shared/test_audit_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/shared/audit_service.py tests/shared/test_audit_service.py
git commit -m "feat(audit): add retention service"
```

---

### Task 6: Test database isolation

**Files:**

- Modify: `tests/conftest.py`
- Test: `tests/test_database_isolation.py`

- [ ] **Step 1: Add session fixture**

In `tests/conftest.py` add:

```python
@pytest.fixture(scope="session", autouse=True)
def clean_test_database():
    if get_settings().environment == "production":
        pytest.fail("Refusing to truncate a production database")
    from lightmes.database import engine
    from lightmes.shared.base import Base
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
```

- [ ] **Step 2: Add sanity test**

Create `tests/test_database_isolation.py`:

```python
def test_database_starts_empty(db_session):
    from sqlalchemy import select, func
    from lightmes.modules.production.models import WorkOrder
    count = db_session.execute(select(func.count()).select_from(WorkOrder)).scalar_one()
    assert count == 0
```

- [ ] **Step 3: Run full suite**

Run: `uv run pytest -q`
Expected: full suite PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_database_isolation.py
git commit -m "test: truncate local test database between sessions"
```

---

## Self-Review

**Spec coverage:** Tasks cover snapshot backfill/accessor, station snapshot usage, stock ledger, return paths, audit retention, and test isolation.  
**Placeholder scan:** Remaining hardcoded `batch_id=1` is called out and should be replaced with real Batch fixtures during execution.  
**Type consistency:** `StockMovement` fields are consistent across model, service, and tests.  
**Migration head:** `74eae97a39cb` is the base before Task 3.
