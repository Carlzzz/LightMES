# Production Closure Design

## Goal

Close the highest-risk gaps left after Phase 1: schema/model consistency, audit attribution, material quantity safety, and basic query surfaces for the new Batch/MaterialLot/StockMovement models.

## Scope

### In Scope

- Align SQLAlchemy models with existing migrations.
- Capture the acting user for session, API v1, and MCP audit writes.
- Register audit listeners for Batch, MaterialLot, StockMovement, BatchMaterialConsumption, IssueAction, and ApiKey.
- Validate MaterialLot receive/consume/return inputs and fix garbled text.
- Add query endpoints and simple HTML pages for StockMovement, MaterialLot, and Batch.
- Expose the realtime shape allowlist and extension registry.

### Out of Scope

- Full inventory management with suppliers, bins, replenishment, and MRP.
- Batch split/merge.
- OEE, maintenance, pallets, packaging, installer, backup/restore.
- Multi-tenant isolation.
- Distributed rate limiting.

## Current State

- `custom_field_definitions` migration has a unique constraint that the model does not declare.
- Audit middleware only reads session user; API/MCP user is set after middleware.
- Audit listeners cover core domain tables but not the new inventory/batch/API key tables.
- `MaterialLotService.return_consumed` has a garbled error string and no positive/upper-bound validation.
- `Batch` and `StockMovement` have no public read UI/API.
- `RealtimeShapeRegistry` and `ExtensionRegistry` are defined but not exposed.

## Design Decisions

### 1. Single audit context

Move audit context population to a small helper that reads either:

- `request.session["user_id"]`
- `request.state.api_key_user_id`
- `request.state.user.id` for MCP

Middleware and API/MCP dependencies must set the same context before SQLAlchemy flush.

### 2. Material quantity invariants

- `receive(quantity > 0)`
- `consume(quantity > 0 and quantity <= available_quantity)`
- `return_consumed(quantity > 0 and quantity <= consumed_quantity_for_lot)`

The service returns domain errors instead of allowing negative or unbounded quantities.

### 3. Read-only query surfaces

- `/inventory/stock-movements`
- `/inventory/material-lots/{lot_id}`
- `/production/batches`

These pages are read-only and role-protected. JSON equivalents use `require_login`.

### 4. Registry exposure

- `GET /api/realtime/shapes` returns shape names and column allowlists.
- `home.html` consumes `extension_registry.all_widgets()` for dashboard widget registration.

## Non-Goals

- Writing full CRUD for Batch or MaterialLot.
- Changing existing public API v1 contracts.
- Introducing a new realtime transport.
