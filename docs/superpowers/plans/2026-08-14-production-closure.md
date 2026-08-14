# Production Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix model/migration drift, attribute audit writes to the acting user, harden material quantity invariants, and expose read-only query surfaces for the new inventory/batch models.

**Architecture:** Keep the existing modular monolith. Extend shared audit context, tighten `MaterialLotService`, add small query routers, and wire registries into the FastAPI app.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2+HTMX, pytest, PostgreSQL.

---

### Task 1: Schema/model and audit attribution alignment

**Files:**

- Modify: `src/lightmes/shared/custom_fields.py`
- Modify: `src/lightmes/shared/audit.py`
- Modify: `src/lightmes/main.py`
- Modify: `src/lightmes/modules/api_v1/dependencies.py`
- Modify: `src/lightmes/modules/agent_gateway/auth.py`
- Test: `tests/shared/test_audit.py`
- Test: `tests/modules/api_v1/test_audit_attribution.py`

- [ ] **Step 1: Add model constraint**

In `CustomFieldDefinition` add:

```python
__table_args__ = (
    UniqueConstraint("entity_type", "key", name="uq_custom_field_entity_key"),
)
```

Import `UniqueConstraint` from sqlalchemy.

- [ ] **Step 2: Add audit context helper**

In `shared/audit.py` add:

```python
def audit_user_from_request(request: Request) -> int | None:
    if "session" in request.scope:
        value = request.session.get("user_id")
        if isinstance(value, int):
            return value
    value = getattr(request.state, "api_key_user_id", None)
    if isinstance(value, int):
        return value
    user = getattr(request.state, "user", None)
    return getattr(user, "id", None)
```

Use it in `AuditContextMiddleware.dispatch`.

- [ ] **Step 3: Set API/MCP user before flush**

In `api_v1/dependencies.py`, after `request.state.api_key_user_id = user.id`, call:

```python
from lightmes.shared.audit import set_audit_user
set_audit_user(user.id)
```

Add `set_audit_user(user_id)` to `shared/audit.py`.

In `agent_gateway/auth.py`, after `request.state.user = user`, call `set_audit_user(user.id)`.

- [ ] **Step 4: Register missing models**

In `main.py` import and add:

```python
_auth_models.ApiKey,
_issue_models.IssueAction,
_production_models.Batch,
_production_models.MaterialLot,
_production_models.StockMovement,
_production_models.BatchMaterialConsumption,
```

to the audit registration tuple.

- [ ] **Step 5: Write tests**

Extend `tests/shared/test_audit.py` with a test that `set_audit_user()` changes context. Add `tests/modules/api_v1/test_audit_attribution.py` using a Bearer API key and asserting a new `AuditLog.user_id` equals the key owner.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/shared/test_audit.py tests/modules/api_v1/test_audit_attribution.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/shared/custom_fields.py src/lightmes/shared/audit.py src/lightmes/main.py src/lightmes/modules/api_v1/dependencies.py src/lightmes/modules/agent_gateway/auth.py tests/shared/test_audit.py tests/modules/api_v1/test_audit_attribution.py
git commit -m "fix(audit): align model constraints and attribute API/MCP users"
```

---

### Task 2: Harden MaterialLotService

**Files:**

- Modify: `src/lightmes/modules/production/material_lot_service.py`
- Test: `tests/modules/inventory/test_material_lot_validation.py`

- [ ] **Step 1: Write failing tests**

Create `tests/modules/inventory/test_material_lot_validation.py`:

```python
import pytest

from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.shared.errors import BusinessRuleError, NotFoundError


def test_consume_rejects_non_positive_quantity(db_session):
    service = MaterialLotService(db_session)
    lot = service.receive(code="VAL-1", product_id=1, quantity=5)
    service.release(lot.code)
    with pytest.raises(BusinessRuleError):
        service.consume(batch_id=1, operation_record_id=None, product_id=1, lot_code="VAL-1", quantity=0)


def test_return_rejects_non_positive_quantity(db_session):
    service = MaterialLotService(db_session)
    lot = service.receive(code="VAL-2", product_id=1, quantity=5)
    with pytest.raises(BusinessRuleError):
        service.return_consumed(material_lot_id=lot.id, quantity=0, reason="bad")
```

Use a real Batch fixture where needed.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/modules/inventory/test_material_lot_validation.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement guards**

In `consume()` add:

```python
if quantity <= 0:
    raise BusinessRuleError("消耗数量必须大于 0")
```

In `return_consumed()` add:

```python
if quantity <= 0:
    raise BusinessRuleError("回补数量必须大于 0")
```

Fix the garbled `NotFoundError` to `物料批次不存在`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/modules/inventory/test_material_lot_validation.py tests/modules/inventory/test_material_lots.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/production/material_lot_service.py tests/modules/inventory/test_material_lot_validation.py
git commit -m "fix(inventory): validate material quantities and fix error text"
```

---

### Task 3: Read-only Batch/StockMovement/MaterialLot surfaces

**Files:**

- Modify: `src/lightmes/modules/inventory/router.py`
- Create: `src/lightmes/templates/inventory/stock_movements.html`
- Create: `src/lightmes/templates/inventory/material_lot_detail.html`
- Create: `src/lightmes/templates/production/batches.html`
- Modify: `src/lightmes/modules/production/router.py`
- Test: `tests/modules/inventory/test_inventory_pages.py`

- [ ] **Step 1: Add StockMovement page**

Create `GET /inventory/stock-movements` returning `templates/inventory/stock_movements.html` with recent movements.

- [ ] **Step 2: Add MaterialLot detail page**

Create `GET /inventory/material-lots/{lot_id}` showing lot fields and its movement history.

- [ ] **Step 3: Add Batch list page**

Create `GET /production/batches` listing batches for a work order, with login guard.

- [ ] **Step 4: Add API endpoints**

Add `GET /api/inventory/stock-movements` and `GET /api/production/batches` with `require_login`.

- [ ] **Step 5: Tests**

Create page tests using existing `client` and `db_session` fixtures, logging in and asserting 200 plus expected labels.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/modules/inventory/test_inventory_pages.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/inventory/router.py src/lightmes/modules/production/router.py src/lightmes/templates/inventory/stock_movements.html src/lightmes/templates/inventory/material_lot_detail.html src/lightmes/templates/production/batches.html tests/modules/inventory/test_inventory_pages.py
git commit -m "feat(inventory): add batch and stock movement read surfaces"
```

---

### Task 4: Realtime shape and extension registry exposure

**Files:**

- Modify: `src/lightmes/main.py`
- Modify: `src/lightmes/templates/home.html`
- Test: `tests/modules/api_v1/test_realtime_shapes.py`

- [ ] **Step 1: Add shape endpoint**

```python
@app.get("/api/realtime/shapes")
def realtime_shapes() -> dict:
    registry = realtime_shape_registry
    return {
        name: {
            "table": shape.table,
            "columns": list(shape.columns),
            "where": shape.where,
        }
        for name, shape in ((n, registry.find(n)) for n in registry.names())
        if shape is not None
    }
```

- [ ] **Step 2: Wire extension registry into home**

In `home()`, pass `widgets=extension_registry.all_widgets()` to `home.html`.

- [ ] **Step 3: Tests**

Add test asserting `/api/realtime/shapes` returns `work_orders_active`, `serial_units_active`, and `defects_open`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/modules/api_v1/test_realtime_shapes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/main.py src/lightmes/templates/home.html tests/modules/api_v1/test_realtime_shapes.py
git commit -m "feat(realtime): expose shape allowlist and extension widgets"
```

---

### Task 5: Full regression

- [ ] Run `uv run pytest -q`
- [ ] Fix any failures caused by this phase
- [ ] Commit remaining test fixes

## Self-Review

**Spec coverage:** Tasks cover model drift, audit attribution, material invariants, read surfaces, and registry exposure.  
**Placeholder scan:** No TODO/TBD in implementation paths.  
**Type consistency:** `MaterialLotService` method names remain `receive`, `release`, `consume`, `return_consumed`.  
**Migration head:** no new migration is required in this phase except if model changes reveal drift.
