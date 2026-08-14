# Production Closure Remaining Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining production-readiness items from the last review, focusing on data invariants, audit query, batch management, material traceability, custom fields, test isolation, and minimal backup/settings operations.

**Architecture:** Continue the modular monolith. Add query/management endpoints in existing modules, migrate startup to a lifespan handler, add optional JSON custom_fields to WorkOrder and MaterialLot, and provide a small settings/backup export API.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2+HTMX, pytest, PostgreSQL.

---

### Task 1: Material return upper-bound validation

**Files:**
- Modify: `src/lightmes/modules/production/material_lot_service.py`
- Test: `tests/modules/inventory/test_material_lot_validation.py`

- [ ] Add a method to compute consumed quantity from `BatchMaterialConsumption` for a lot.
- [ ] In `return_consumed`, reject return quantity greater than consumed quantity.
- [ ] Add test for over-return.
- [ ] Run inventory tests.
- [ ] Commit.

### Task 2: Audit query UI/API

**Files:**
- Create: `src/lightmes/shared/audit_router.py` or add in auth/system module.
- Create: `src/lightmes/templates/system/audit_logs.html`
- Test: `tests/shared/test_audit_query.py`

- [ ] Add `GET /system/audit-logs` page with entity/user/action filters.
- [ ] Add `GET /api/system/audit-logs` JSON endpoint using `require_role("admin")`.
- [ ] Test access control and pagination.
- [ ] Commit.

### Task 3: Migrate startup to lifespan

**Files:**
- Modify: `src/lightmes/main.py`
- Test: `tests/test_health.py`

- [ ] Replace `@app.on_event("startup")` with `lifespan` asynccontextmanager.
- [ ] Ensure initialization logic is unchanged.
- [ ] Run health/startup tests.
- [ ] Commit.

### Task 4: Batch management API and basic UI

**Files:**
- Modify: `src/lightmes/modules/production/batch_service.py`
- Modify: `src/lightmes/modules/production/router.py`
- Modify: `src/lightmes/templates/production/batches.html`
- Test: `tests/modules/production/test_batch_management.py`

- [ ] Add Batch list/show API and page.
- [ ] Add cancel/complete endpoints guarded by supervisor/admin.
- [ ] Test transitions.
- [ ] Commit.

### Task 5: MaterialLot traceability endpoint

**Files:**
- Modify: `src/lightmes/modules/inventory/router.py`
- Modify: `src/lightmes/modules/production/repository.py`
- Test: `tests/modules/inventory/test_material_lot_trace.py`

- [ ] Add `GET /api/inventory/material-lots/{lot_id}/usage` returning consumed batches, work orders, and serial units.
- [ ] Add page link in material lot detail.
- [ ] Test.
- [ ] Commit.

### Task 6: Custom fields integration

**Files:**
- Modify: `src/lightmes/modules/production/models.py`
- Modify: `src/lightmes/modules/masterdata/models.py` if needed
- Modify: `src/lightmes/shared/custom_fields.py`
- Test: `tests/shared/test_custom_fields.py`

- [ ] Add `custom_fields: Mapped[dict | None] = mapped_column(JSON, default=None)` to `WorkOrder` and `MaterialLot`.
- [ ] Add migration.
- [ ] Add service validation/cast methods and tests.
- [ ] Commit.

### Task 7: Dedicated test database URL

**Files:**
- Modify: `tests/conftest.py`
- Modify: `.env.example`

- [ ] Add `TEST_DATABASE_URL` setting support and use a separate database in tests when configured.
- [ ] Prevent local test truncation from using the normal `DATABASE_URL` accidentally.
- [ ] Run test isolation.
- [ ] Commit.

### Task 8: Minimal settings export and backup

**Files:**
- Create: `src/lightmes/modules/system/backup.py`
- Modify: `src/lightmes/main.py`
- Test: `tests/modules/system/test_backup.py`

- [ ] Add admin-only `GET /api/system/settings/export` returning non-secret settings.
- [ ] Add admin-only `GET /api/system/db-dump` guarded by a CLI-only secret or explicit setting.
- [ ] Keep scope minimal and documented.
- [ ] Commit.

### Task 9: Full regression

- [ ] Run `uv run pytest -q`.
- [ ] Fix failures.
- [ ] Commit.
