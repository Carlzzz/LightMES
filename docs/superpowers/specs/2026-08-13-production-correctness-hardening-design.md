# Production Correctness Hardening Design

## Goal

Make the newly added process snapshot, audit, batch, and material-lot foundations production-safe before expanding UI/API further. This phase only fixes correctness and data-integrity gaps.

## Scope

### In Scope

- Backfill immutable process snapshots for existing active work orders.
- Ensure station view, WIP, and planner read the same snapshot as pass validation.
- Add a stock movement ledger so every MaterialLot quantity change is reconstructable.
- Add material return/reversal paths for rework, unbind, and failed operations.
- Add audit-log query/retention basics.
- Harden test isolation so the full suite no longer depends on an existing local Postgres database.

### Out of Scope

- Batch split/merge and full batch management UI.
- Full inventory UI with suppliers, bins, replenishment, and MRP.
- OEE, maintenance, pallets, packaging, installer/backup/restore.
- Custom-field entity integration and realtime endpoint exposure.

## Current State

- `WorkOrder.process_snapshot` exists and is captured at release in `ProductionService.release_work_order`.
- `OperationPassService` prefers snapshot data.
- `StationService` still reads live `MasterDataQueryService`.
- `MaterialLotService.receive/consume` changes `available_quantity` without a movement ledger.
- `GenealogyService.unbind` and defect disposition do not return consumed material lots.
- `AuditLog` exists but has no API/UI or retention.
- Tests use a shared local Postgres database and fixed serial numbers.

## Design Decisions

### 1. Snapshot accessor

Introduce one function:

```python
get_work_order_process(wo) -> WorkOrderProcess
```

`WorkOrderProcess` exposes operations, BOM items, and allowed workstation ids. It returns snapshot data when present and falls back to live master data only for legacy work orders with no snapshot.

### 2. Stock movement ledger

Every change to `MaterialLot.quantity` or `MaterialLot.available_quantity` writes a `StockMovement` row in the same transaction. Movement types are:

- `receive`
- `release`
- `consume`
- `return`
- `adjustment`

### 3. Material reversal

When a component bind is unbound or a work order is reworked/scrapped, `MaterialLotService.return_consumed()` restores the consumed quantity and writes a `return` movement.

### 4. Audit retention

Add `AuditLogService.prune_old(days=365)` and an admin JSON API endpoint. Retention is invoked manually or from a scheduled task; no automatic scheduler is introduced in this phase.

### 5. Test isolation

Add a pytest fixture that truncates all domain tables at the start of the test session. This is acceptable for a local test database only and must not run in production.

## Non-Goals

- Multi-tenant behavior.
- Distributed transactions.
- Realtime sync transport.
- Public inventory API v1.
