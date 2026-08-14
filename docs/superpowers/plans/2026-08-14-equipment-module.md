# Equipment Runtime Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `equipment` module that tracks workstation state time-slices, auto-records downtime, and computes OEE availability + quality, wired to the existing connectivity pipeline.

**Architecture:** New module `src/lightmes/modules/equipment/` (models/tag_service/state_machine/ingestor/downtime_service/oee_service/monitor_service/router). The connectivity layer calls `ingest_topic_signals()` synchronously (function-level lazy import to avoid circular import) inside `persist_message` after business-action execution — this deviates from the spec's "event bus" wording, see note below. State is stored as open/closed time-slice rows (`WorkstationState`), never as a mutable "current state" column.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, Jinja2 templates, pytest + PostgreSQL test DB.

## Architecture note: signal forwarding uses synchronous call, not event bus

The spec described connectivity publishing a `MachineSignalReceived` event that equipment subscribes to. During planning this was simplified to a **direct synchronous call** for two reasons:

1. **Transactional atomicity** — signal ingestion must land in the same DB transaction as the `MachineMessage` row, otherwise a message can be persisted while its state transition fails (or vice versa). An event-bus listener opening its own session breaks that atomicity.
2. **The event bus never actually removed the import dependency** — extracting a tag still requires importing `MachineTag`/`TagService` from equipment, so connectivity already depends on equipment at runtime. Function-level lazy import (already the codebase's pattern in `action_executor.py`) is what actually prevents a circular import.

The event bus remains in use **inside** equipment (e.g. a `WorkstationStateChanged` event is published on transition, for future WebSocket/notification subscribers), but is not the connectivity→equipment transport.

---

## Global Constraints

- Version floor: Python 3.12, FastMCP not involved here.
- All models inherit `Base` + `TimestampMixin` from `lightmes.shared.base`.
- SQLAlchemy 2.0 style: `Mapped[...]` + `mapped_column(...)`, no legacy `Column(...)`.
- Errors raise `lightmes.shared.errors` subclasses (`ValidationError`=400, `NotFoundError`=404, `BusinessRuleError`=422).
- HTML routes use `html_role_guard(request, db, "admin", "supervisor", ...)` returning `(user, response)`; JSON routes use `require_role(...)` / `require_login` as FastAPI dependencies.
- Role levels (from `auth/dependencies.py`): `viewer`=10, `operator`=20, `supervisor`=30, `admin`=40.
- Migration `down_revision` chain head is **`f230300852cb`** (single linear chain — confirmed via `alembic history`).
- Never raise from the signal pipeline; every tag extraction/ingest is wrapped in try/except with `logger.warning`.
- Alembic command: `uv run alembic revision -m "..."` auto-generates the revision id; paste the provided `upgrade`/`downgrade` bodies.
- Test command: `uv run pytest tests/modules/equipment/<file>.py -v` (uses dedicated PostgreSQL test DB via `tests/conftest.py`).

---

## File Structure

**Create:**
- `src/lightmes/modules/equipment/__init__.py` — `register(app)` + `SYSTEM_DOWNTIME_REASONS` + `ensure_system_downtime_reasons(db)`
- `src/lightmes/modules/equipment/models.py` — 4 tables + state constants
- `src/lightmes/modules/equipment/schemas.py` — Pydantic I/O
- `src/lightmes/modules/equipment/tag_service.py` — `TagService` (CRUD + `apply_transform`)
- `src/lightmes/modules/equipment/state_machine.py` — `WorkstationStateMachine`
- `src/lightmes/modules/equipment/ingestor.py` — `MachineSignalIngestor` + `ingest_topic_signals()`
- `src/lightmes/modules/equipment/events.py` — `WorkstationStateChanged` event
- `src/lightmes/modules/equipment/downtime_service.py` — `DowntimeService`
- `src/lightmes/modules/equipment/oee_service.py` — `OeeService` + pure compute functions
- `src/lightmes/modules/equipment/monitor_service.py` — `MonitorService`
- `src/lightmes/modules/equipment/router.py` — HTML + JSON routes
- `src/lightmes/migrations/versions/<auto>_create_equipment_tables.py`
- `src/lightmes/templates/equipment/monitor.html`
- `src/lightmes/templates/equipment/downtimes.html`
- `src/lightmes/templates/equipment/oee.html`
- `src/lightmes/templates/equipment/tags.html`
- `src/lightmes/templates/equipment/downtime_reasons.html`
- `tests/modules/equipment/test_tag_transform.py`
- `tests/modules/equipment/test_state_machine.py`
- `tests/modules/equipment/test_ingestor.py`
- `tests/modules/equipment/test_signal_pipeline.py`
- `tests/modules/equipment/test_oee_service.py`
- `tests/modules/equipment/test_pages.py`

**Modify:**
- `src/lightmes/modules/connectivity/models.py` — `MachineConnection` add `work_station_id`
- `src/lightmes/modules/connectivity/mqtt_listener/message_service.py` — call `ingest_topic_signals`
- `src/lightmes/config.py` — add `equipment_auto_create_issue_on_fault`
- `src/lightmes/main.py` — register module + lifespan ensure reasons
- `tests/conftest.py` — import equipment models so FK metadata resolves

---

## Task 1: Data models, migration, and module scaffold

**Files:**
- Create: `src/lightmes/modules/equipment/__init__.py`, `src/lightmes/modules/equipment/models.py`
- Create: migration (via `alembic revision`)
- Modify: `src/lightmes/modules/connectivity/models.py`, `src/lightmes/config.py`, `src/lightmes/main.py`, `tests/conftest.py`
- Test: `tests/modules/equipment/test_migration.py`

**Interfaces:**
- Produces (used by all later tasks): `MachineTag`, `WorkstationState`, `ProductionDowntime`, `DowntimeReason` model classes; module constants `ALL_STATES`, `LOSS_STATES`, `PLANNED_STATES`, `DOWNTIME_STATES`; `ensure_system_downtime_reasons(db)`; `register(app)`.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_migration.py`:

```python
from sqlalchemy import inspect


def test_equipment_tables_exist(db_session):
    from lightmes.database import engine

    names = set(inspect(engine).get_table_names())
    for t in ("machine_tags", "workstation_states", "production_downtimes", "downtime_reasons"):
        assert t in names, f"missing table {t}"


def test_machine_connections_has_work_station_id(db_session):
    from sqlalchemy import inspect
    from lightmes.database import engine

    cols = {c["name"] for c in inspect(engine).get_columns("machine_connections")}
    assert "work_station_id" in cols


def test_ensure_system_downtime_reasons(db_session):
    from lightmes.modules.equipment import ensure_system_downtime_reasons
    from lightmes.modules.equipment.models import DowntimeReason
    from sqlalchemy import select

    ensure_system_downtime_reasons(db_session)
    db_session.flush()
    codes = set(db_session.execute(select(DowntimeReason.code)).scalars().all())
    assert {"AUTO-FAULT", "AUTO-STOP", "AUTO-WAIT", "AUTO-CLEAN", "AUTO-MAINT"} <= codes
    # idempotent
    ensure_system_downtime_reasons(db_session)
    count = db_session.execute(select(DowntimeReason)).scalars().all()
    assert len([r for r in count if r.code.startswith("AUTO-")]) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_migration.py -v`
Expected: FAIL — `No module named 'lightmes.modules.equipment'` (or import error).

- [ ] **Step 3: Create `models.py`**

Create `src/lightmes/modules/equipment/models.py`:

```python
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from lightmes.shared.base import Base, TimestampMixin


# ── State constants (module-level, matching defect_service's style) ─────────
RUNNING = "RUNNING"
IDLE = "IDLE"
STOPPED = "STOPPED"
FAULT = "FAULT"
SETUP = "SETUP"
WAITING = "WAITING"
CLEANING = "CLEANING"
MAINTENANCE = "MAINTENANCE"

ALL_STATES = [RUNNING, IDLE, STOPPED, FAULT, SETUP, WAITING, CLEANING, MAINTENANCE]
# unplanned availability loss
LOSS_STATES = [STOPPED, FAULT, WAITING]
# scheduled downtime (not an availability loss)
PLANNED_STATES = [CLEANING, MAINTENANCE]
# every state that auto-opens a ProductionDowntime
DOWNTIME_STATES = LOSS_STATES + PLANNED_STATES

SIGNAL_TYPES = ["state", "good_count", "reject_count", "cycle_complete", "telemetry", "alarm"]

_STATE_CK = "state IN ('RUNNING','IDLE','STOPPED','FAULT','SETUP','WAITING','CLEANING','MAINTENANCE')"


class MachineTag(Base, TimestampMixin):
    __tablename__ = "machine_tags"
    __table_args__ = (
        UniqueConstraint("machine_topic_id", "field_path", "signal_type",
                         name="uq_machine_tag_topic_field_signal"),
        CheckConstraint(
            "signal_type IN ('state','good_count','reject_count','cycle_complete','telemetry','alarm')",
            name="ck_machine_tags_signal_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_topic_id: Mapped[int] = mapped_column(
        ForeignKey("machine_topics.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    field_path: Mapped[str] = mapped_column(String(255))
    signal_type: Mapped[str] = mapped_column(String(20))
    data_type: Mapped[str | None] = mapped_column(String(20), default=None)
    transform: Mapped[dict | None] = mapped_column(JSON, default=None)
    unit: Mapped[str | None] = mapped_column(String(20), default=None)
    last_count_value: Mapped[int | None] = mapped_column(Integer, default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class WorkstationState(Base, TimestampMixin):
    __tablename__ = "workstation_states"
    __table_args__ = (
        Index("ix_ws_state_station_started", "work_station_id", "started_at"),
        Index("ix_ws_state_station_ended", "work_station_id", "ended_at"),
        CheckConstraint(_STATE_CK, name="ck_workstation_states_state"),
        CheckConstraint("source IN ('machine','manual')", name="ck_workstation_states_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"), index=True)
    state: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    source: Mapped[str] = mapped_column(String(20), default="machine")
    metadata: Mapped[dict | None] = mapped_column(JSON, default=None)


class ProductionDowntime(Base, TimestampMixin):
    __tablename__ = "production_downtimes"
    __table_args__ = (
        Index("ix_downtime_station", "work_station_id"),
        Index("ix_downtime_line", "line_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"), index=True)
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"), index=True)
    downtime_reason_id: Mapped[int | None] = mapped_column(
        ForeignKey("downtime_reasons.id"), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    is_planned: Mapped[bool] = mapped_column(default=False)


class DowntimeReason(Base, TimestampMixin):
    __tablename__ = "downtime_reasons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_downtime_reason_code"),
        CheckConstraint("kind IN ('planned','unplanned')", name="ck_downtime_reason_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_system: Mapped[bool] = mapped_column(default=False)
```

- [ ] **Step 4: Create `__init__.py`**

Create `src/lightmes/modules/equipment/__init__.py`:

```python
from fastapi import FastAPI

from lightmes.modules.equipment.models import DowntimeReason

SYSTEM_DOWNTIME_REASONS = [
    {"code": "AUTO-FAULT", "name": "设备故障(自动)", "kind": "unplanned"},
    {"code": "AUTO-STOP", "name": "设备停机(自动)", "kind": "unplanned"},
    {"code": "AUTO-WAIT", "name": "设备等待(自动)", "kind": "unplanned"},
    {"code": "AUTO-CLEAN", "name": "设备清洁(自动)", "kind": "planned"},
    {"code": "AUTO-MAINT", "name": "设备保养(自动)", "kind": "planned"},
]


def ensure_system_downtime_reasons(db) -> None:
    """幂等创建 + 激活系统停机原因（启动时调用）。"""
    from sqlalchemy import select

    for spec in SYSTEM_DOWNTIME_REASONS:
        r = db.execute(
            select(DowntimeReason).where(DowntimeReason.code == spec["code"])
        ).scalar_one_or_none()
        if r is None:
            r = DowntimeReason(
                code=spec["code"], name=spec["name"], kind=spec["kind"],
                is_active=True, is_system=True,
            )
            db.add(r)
        else:
            r.is_active = True
    db.flush()


def register(app: FastAPI) -> None:
    from lightmes.modules.equipment.router import router

    app.include_router(router)
```

- [ ] **Step 5: Modify `connectivity/models.py` — add `work_station_id`**

Add one line inside `class MachineConnection` (after `messages_received`):

```python
    work_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), default=None, index=True)
```

- [ ] **Step 6: Modify `config.py` — add setting**

Add after `rate_limit_window_seconds`:

```python
    equipment_auto_create_issue_on_fault: bool = False
```

- [ ] **Step 7: Generate and fill the migration**

Run: `uv run alembic revision -m "create_equipment_tables"`

Then replace the generated file's `upgrade()`/`downgrade()` bodies with:

```python
def upgrade() -> None:
    op.create_table(
        "machine_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("machine_topic_id", sa.Integer(), sa.ForeignKey("machine_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("field_path", sa.String(255), nullable=False),
        sa.Column("signal_type", sa.String(20), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=True),
        sa.Column("transform", sa.JSON(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("last_count_value", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("machine_topic_id", "field_path", "signal_type", name="uq_machine_tag_topic_field_signal"),
        sa.CheckConstraint("signal_type IN ('state','good_count','reject_count','cycle_complete','telemetry','alarm')", name="ck_machine_tags_signal_type"),
    )
    op.create_index("ix_machine_tags_machine_topic_id", "machine_tags", ["machine_topic_id"])

    op.create_table(
        "workstation_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default=sa.text("'machine'")),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("state IN ('RUNNING','IDLE','STOPPED','FAULT','SETUP','WAITING','CLEANING','MAINTENANCE')", name="ck_workstation_states_state"),
        sa.CheckConstraint("source IN ('machine','manual')", name="ck_workstation_states_source"),
    )
    op.create_index("ix_workstation_states_work_station_id", "workstation_states", ["work_station_id"])
    op.create_index("ix_ws_state_station_started", "workstation_states", ["work_station_id", "started_at"])
    op.create_index("ix_ws_state_station_ended", "workstation_states", ["work_station_id", "ended_at"])

    op.create_table(
        "downtime_reasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_downtime_reason_code"),
        sa.CheckConstraint("kind IN ('planned','unplanned')", name="ck_downtime_reason_kind"),
    )

    op.create_table(
        "production_downtimes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("lines.id"), nullable=False),
        sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=False),
        sa.Column("downtime_reason_id", sa.Integer(), sa.ForeignKey("downtime_reasons.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_planned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_production_downtimes_work_station_id", "production_downtimes", ["work_station_id"])
    op.create_index("ix_production_downtimes_line_id", "production_downtimes", ["line_id"])
    op.create_index("ix_downtime_station", "production_downtimes", ["work_station_id"])
    op.create_index("ix_downtime_line", "production_downtimes", ["line_id"])

    op.add_column("machine_connections", sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=True))
    op.create_index("ix_machine_connections_work_station_id", "machine_connections", ["work_station_id"])


def downgrade() -> None:
    op.drop_index("ix_machine_connections_work_station_id", table_name="machine_connections")
    op.drop_column("machine_connections", "work_station_id")
    op.drop_index("ix_downtime_line", table_name="production_downtimes")
    op.drop_index("ix_downtime_station", table_name="production_downtimes")
    op.drop_index("ix_production_downtimes_line_id", table_name="production_downtimes")
    op.drop_index("ix_production_downtimes_work_station_id", table_name="production_downtimes")
    op.drop_table("production_downtimes")
    op.drop_table("downtime_reasons")
    op.drop_index("ix_ws_state_station_ended", table_name="workstation_states")
    op.drop_index("ix_ws_state_station_started", table_name="workstation_states")
    op.drop_index("ix_workstation_states_work_station_id", table_name="workstation_states")
    op.drop_table("workstation_states")
    op.drop_index("ix_machine_tags_machine_topic_id", table_name="machine_tags")
    op.drop_table("machine_tags")
```

- [ ] **Step 8: Modify `main.py` — register module + lifespan ensure**

Add `equipment` to the import block (line ~16-29), add `equipment.register(app)` after `connectivity.register(app)`, and add ensure call inside lifespan:

```python
from lightmes.modules.equipment import ensure_system_downtime_reasons
# ...inside lifespan, after DefectService ensure:
        ensure_system_downtime_reasons(db)
```

- [ ] **Step 9: Modify `tests/conftest.py` — register equipment models**

Add after the `_connectivity_models` import line (~15):

```python
from lightmes.modules.equipment import models as _equipment_models  # noqa: F401
```

- [ ] **Step 10: Run migration + test**

Run:
```bash
uv run alembic upgrade head
uv run pytest tests/modules/equipment/test_migration.py -v
```
Expected: PASS (all 3 tests).

- [ ] **Step 11: Commit**

```bash
git add src/lightmes/modules/equipment/ src/lightmes/migrations/versions/ src/lightmes/modules/connectivity/models.py src/lightmes/config.py src/lightmes/main.py tests/conftest.py tests/modules/equipment/test_migration.py
git commit -m "feat(equipment): add models, migration, and module scaffold"
```

---

## Task 2: TagService with applyTransform

**Files:**
- Create: `src/lightmes/modules/equipment/schemas.py`, `src/lightmes/modules/equipment/tag_service.py`
- Test: `tests/modules/equipment/test_tag_transform.py`

**Interfaces:**
- Consumes: `MachineTag` model (Task 1).
- Produces: `TagService(db)` with `.apply_transform(tag, raw) -> any`, `.list_active_for_topic(topic_id) -> list[MachineTag]`, `.create(...)`, `.update(...)`, `.delete(...)`.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_tag_transform.py`:

```python
import pytest

from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.tag_service import TagService


def _tag(transform=None):
    return MachineTag(machine_topic_id=1, name="t", field_path="$.s",
                      signal_type="state", transform=transform)


def test_value_map_matches_key(db_session):
    tag = _tag({"value_map": {"1": "RUNNING", "2": "IDLE"}})
    assert TagService(db_session).apply_transform(tag, 1) == "RUNNING"
    assert TagService(db_session).apply_transform(tag, "2") == "IDLE"


def test_value_map_default(db_session):
    tag = _tag({"value_map": {"1": "RUNNING", "default": "UNKNOWN"}})
    assert TagService(db_session).apply_transform(tag, 99) == "UNKNOWN"


def test_value_map_no_match_no_default(db_session):
    tag = _tag({"value_map": {"1": "RUNNING"}})
    assert TagService(db_session).apply_transform(tag, 99) == 99


def test_scale_offset(db_session):
    tag = _tag({"scale": 0.1, "offset": -50})
    assert TagService(db_session).apply_transform(tag, 1000) == 50.0


def test_numeric_string_coerced(db_session):
    tag = _tag({"scale": 2})
    assert TagService(db_session).apply_transform(tag, "25") == 50.0


def test_non_numeric_passthrough(db_session):
    tag = _tag()
    assert TagService(db_session).apply_transform(tag, "hello") == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_tag_transform.py -v`
Expected: FAIL — `No module named 'lightmes.modules.equipment.tag_service'`.

- [ ] **Step 3: Create `schemas.py`**

Create `src/lightmes/modules/equipment/schemas.py`:

```python
from pydantic import BaseModel


class TagCreate(BaseModel):
    machine_topic_id: int
    name: str
    field_path: str
    signal_type: str
    data_type: str | None = None
    transform: dict | None = None
    unit: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    field_path: str | None = None
    signal_type: str | None = None
    data_type: str | None = None
    transform: dict | None = None
    unit: str | None = None
    is_active: bool | None = None
```

- [ ] **Step 4: Create `tag_service.py`**

Create `src/lightmes/modules/equipment/tag_service.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import SIGNAL_TYPES, MachineTag
from lightmes.modules.equipment.schemas import TagCreate, TagUpdate
from lightmes.shared.errors import NotFoundError, ValidationError


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


class TagService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def apply_transform(self, tag: MachineTag, raw):
        """Apply value_map / scale / offset transform. Never raises."""
        t = tag.transform or {}
        if t.get("value_map"):
            vm = t["value_map"]
            key = "1" if raw is True else ("0" if raw is False else str(raw))
            if key in vm:
                return vm[key]
            if "default" in vm:
                return vm["default"]
            return raw
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and _is_number(raw)):
            value = float(raw)
            if t.get("scale") is not None:
                value *= float(t["scale"])
            if t.get("offset") is not None:
                value += float(t["offset"])
            return value
        return raw

    def list_active_for_topic(self, topic_id: int) -> list[MachineTag]:
        return list(self.db.execute(
            select(MachineTag).where(
                MachineTag.machine_topic_id == topic_id,
                MachineTag.is_active.is_(True),
            )
        ).scalars().all())

    def get(self, tag_id: int) -> MachineTag:
        tag = self.db.get(MachineTag, tag_id)
        if tag is None:
            raise NotFoundError(f"信号标签不存在: {tag_id}")
        return tag

    def create(self, data: TagCreate) -> MachineTag:
        if data.signal_type not in SIGNAL_TYPES:
            raise ValidationError(f"signal_type 必须是 {SIGNAL_TYPES} 之一: {data.signal_type}")
        tag = MachineTag(**data.model_dump())
        self.db.add(tag)
        self.db.flush()
        return tag

    def update(self, tag_id: int, data: TagUpdate) -> MachineTag:
        tag = self.get(tag_id)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(tag, k, v)
        if tag.signal_type not in SIGNAL_TYPES:
            raise ValidationError(f"signal_type 必须是 {SIGNAL_TYPES} 之一")
        self.db.flush()
        return tag

    def delete(self, tag_id: int) -> None:
        tag = self.get(tag_id)
        self.db.delete(tag)
        self.db.flush()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_tag_transform.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/equipment/schemas.py src/lightmes/modules/equipment/tag_service.py tests/modules/equipment/test_tag_transform.py
git commit -m "feat(equipment): add TagService with applyTransform"
```

---

## Task 3: WorkstationStateMachine

**Files:**
- Create: `src/lightmes/modules/equipment/state_machine.py`
- Test: `tests/modules/equipment/test_state_machine.py`

**Interfaces:**
- Consumes: `WorkstationState`, `ProductionDowntime`, `DowntimeReason`, state constants (Task 1).
- Produces: `WorkstationStateMachine(db)` with `.transition(work_station_id, new_state, *, source, metadata, at) -> WorkstationState` and `.current(work_station_id) -> WorkstationState | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_state_machine.py`:

```python
from datetime import datetime, timedelta, timezone

from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.masterdata.models import Line, WorkStation

T0 = datetime(2026, 8, 14, 8, 0, 0, tzinfo=timezone.utc)


def _ws(db_session):
    line = Line(code="L_EQ", name="L_EQ")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_EQ", name="WS_EQ", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    return ws


def test_transition_opens_new_state(db_session):
    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    st = sm.transition(ws.id, "RUNNING", at=T0)
    assert st.state == "RUNNING"
    assert st.ended_at is None
    assert sm.current(ws.id).state == "RUNNING"


def test_transition_closes_previous(db_session):
    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "IDLE", at=T0 + timedelta(seconds=60))
    cur = sm.current(ws.id)
    assert cur.state == "IDLE"
    # previous closed with duration
    prev = db_session.query(
        type(cur)).filter_by(work_station_id=ws.id, state="RUNNING").one()
    assert prev.ended_at is not None
    assert prev.duration_seconds == 60


def test_same_state_noop_merges_metadata(db_session):
    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0, metadata={"a": 1})
    st = sm.transition(ws.id, "RUNNING", at=T0 + timedelta(seconds=5), metadata={"b": 2})
    # same row, no new row
    assert st.metadata == {"a": 1, "b": 2}
    rows = db_session.query(type(st)).filter_by(work_station_id=ws.id).all()
    assert len(rows) == 1


def test_fault_opens_unplanned_downtime(db_session):
    from lightmes.modules.equipment.models import ProductionDowntime

    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "FAULT", at=T0 + timedelta(minutes=10))
    dt = db_session.query(ProductionDowntime).filter_by(work_station_id=ws.id).one()
    assert dt.ended_at is None
    assert dt.is_planned is False
    assert dt.downtime_reason.code == "AUTO-FAULT"


def test_leaving_fault_closes_downtime(db_session):
    from lightmes.modules.equipment.models import ProductionDowntime

    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "FAULT", at=T0 + timedelta(minutes=10))
    sm.transition(ws.id, "RUNNING", at=T0 + timedelta(minutes=25))
    dt = db_session.query(ProductionDowntime).filter_by(work_station_id=ws.id).one()
    assert dt.ended_at is not None
    assert dt.duration_minutes == 15


def test_maintenance_is_planned(db_session):
    from lightmes.modules.equipment.models import ProductionDowntime

    ensure_system_downtime_reasons(db_session)
    ws = _ws(db_session)
    sm = WorkstationStateMachine(db_session)
    sm.transition(ws.id, "RUNNING", at=T0)
    sm.transition(ws.id, "MAINTENANCE", at=T0 + timedelta(minutes=10))
    dt = db_session.query(ProductionDowntime).filter_by(work_station_id=ws.id).one()
    assert dt.is_planned is True
    assert dt.downtime_reason.code == "AUTO-MAINT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_state_machine.py -v`
Expected: FAIL — `No module named 'lightmes.modules.equipment.state_machine'`.

- [ ] **Step 3: Create `state_machine.py`**

Create `src/lightmes/modules/equipment/state_machine.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import (
    ALL_STATES, DOWNTIME_STATES, PLANNED_STATES,
    DowntimeReason, ProductionDowntime, WorkstationState,
)
from lightmes.modules.masterdata.models import WorkStation
from lightmes.shared.errors import BusinessRuleError

_AUTO_REASON_BY_STATE = {
    "FAULT": "AUTO-FAULT",
    "STOPPED": "AUTO-STOP",
    "WAITING": "AUTO-WAIT",
    "CLEANING": "AUTO-CLEAN",
    "MAINTENANCE": "AUTO-MAINT",
}


class WorkstationStateMachine:
    def __init__(self, db: Session) -> None:
        self.db = db

    def current(self, work_station_id: int) -> WorkstationState | None:
        return self.db.execute(
            select(WorkstationState)
            .where(WorkstationState.work_station_id == work_station_id,
                   WorkstationState.ended_at.is_(None))
            .order_by(WorkstationState.started_at.desc())
        ).scalars().first()

    def transition(self, work_station_id: int, new_state: str, *,
                   source: str = "machine", metadata: dict | None = None,
                   at: datetime | None = None) -> WorkstationState:
        if new_state not in ALL_STATES:
            raise BusinessRuleError(f"未知设备状态: {new_state}")
        at = at or datetime.now(timezone.utc)

        # 锁当前 open 行，防并发 transition 竞争
        current = self.db.execute(
            select(WorkstationState)
            .where(WorkstationState.work_station_id == work_station_id,
                   WorkstationState.ended_at.is_(None))
            .order_by(WorkstationState.started_at.desc())
            .with_for_update()
        ).scalars().first()

        if current is not None and current.state == new_state:
            if metadata:
                current.metadata = {**(current.metadata or {}), **metadata}
            return current

        if current is not None:
            current.ended_at = at
            current.duration_seconds = max(0, int((at - current.started_at).total_seconds()))
            self._close_open_downtime(work_station_id, at)

        line_id = self._line_id_for(work_station_id)
        state = WorkstationState(
            work_station_id=work_station_id, state=new_state,
            started_at=at, source=source, metadata=metadata,
        )
        self.db.add(state)
        self.db.flush()

        if new_state in DOWNTIME_STATES:
            self._open_downtime(work_station_id, new_state, at, line_id)

        return state

    def _line_id_for(self, work_station_id: int) -> int | None:
        ws = self.db.get(WorkStation, work_station_id)
        return ws.line_id if ws is not None else None

    def _open_downtime(self, work_station_id: int, state: str, at: datetime,
                       line_id: int | None) -> None:
        existing = self.db.execute(
            select(ProductionDowntime).where(
                ProductionDowntime.work_station_id == work_station_id,
                ProductionDowntime.ended_at.is_(None),
            )
        ).scalars().first()
        if existing is not None:
            return
        reason = self._auto_reason_for(state)
        self.db.add(ProductionDowntime(
            line_id=line_id,
            work_station_id=work_station_id,
            downtime_reason_id=reason.id,
            started_at=at,
            is_planned=state in PLANNED_STATES,
            notes=f"Auto-recorded from machine state {state}",
        ))

    def _close_open_downtime(self, work_station_id: int, at: datetime) -> None:
        open_dt = self.db.execute(
            select(ProductionDowntime).where(
                ProductionDowntime.work_station_id == work_station_id,
                ProductionDowntime.ended_at.is_(None),
            ).order_by(ProductionDowntime.started_at.desc())
        ).scalars().first()
        if open_dt is not None:
            open_dt.ended_at = at
            open_dt.duration_minutes = max(0, int((at - open_dt.started_at).total_seconds() // 60))

    def _auto_reason_for(self, state: str) -> DowntimeReason:
        code = _AUTO_REASON_BY_STATE.get(state, "AUTO-STOP")
        reason = self.db.execute(
            select(DowntimeReason).where(DowntimeReason.code == code)
        ).scalars().one()
        return reason
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_state_machine.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/equipment/state_machine.py tests/modules/equipment/test_state_machine.py
git commit -m "feat(equipment): add WorkstationStateMachine with auto downtime"
```

---

## Task 4: MachineSignalIngestor

**Files:**
- Create: `src/lightmes/modules/equipment/ingestor.py`
- Test: `tests/modules/equipment/test_ingestor.py`

**Interfaces:**
- Consumes: `TagService` (Task 2), `WorkstationStateMachine` (Task 3).
- Produces: `MachineSignalIngestor(db)` with `.ingest(tag, raw_value, work_station_id) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_ingestor.py`:

```python
from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.ingestor import MachineSignalIngestor
from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.masterdata.models import Line, WorkStation


def _setup(db_session):
    ensure_system_downtime_reasons(db_session)
    line = Line(code="L_IG", name="L_IG")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_IG", name="WS_IG", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    return ws


def test_state_signal_transitions(db_session):
    ws = _setup(db_session)
    tag = MachineTag(machine_topic_id=1, name="state", field_path="$.s",
                     signal_type="state", transform={"value_map": {"1": "RUNNING", "2": "FAULT"}})
    db_session.add(tag); db_session.flush()

    ing = MachineSignalIngestor(db_session)
    ing.ingest(tag, "1", ws.id)
    assert WorkstationStateMachine(db_session).current(ws.id).state == "RUNNING"
    ing.ingest(tag, "2", ws.id)
    assert WorkstationStateMachine(db_session).current(ws.id).state == "FAULT"


def test_count_signal_delta_and_reset(db_session):
    ws = _setup(db_session)
    tag = MachineTag(machine_topic_id=1, name="good", field_path="$.g",
                     signal_type="good_count")
    db_session.add(tag); db_session.flush()

    ing = MachineSignalIngestor(db_session)
    ing.ingest(tag, 100, ws.id)
    assert tag.last_count_value == 100
    ing.ingest(tag, 105, ws.id)
    assert tag.last_count_value == 105
    # reset (device reboot)
    ing.ingest(tag, 3, ws.id)
    assert tag.last_count_value == 3


def test_telemetry_writes_metadata(db_session):
    ws = _setup(db_session)
    tag = MachineTag(machine_topic_id=1, name="temp", field_path="$.t",
                     signal_type="telemetry", unit="C")
    db_session.add(tag); db_session.flush()
    sm = WorkstationStateMachine(db_session)
    from datetime import datetime, timezone
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    ing = MachineSignalIngestor(db_session)
    ing.ingest(tag, 72.5, ws.id)
    cur = sm.current(ws.id)
    assert cur.metadata["temp"] == 72.5
    assert cur.metadata["temp_unit"] == "C"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_ingestor.py -v`
Expected: FAIL — `No module named 'lightmes.modules.equipment.ingestor'`.

- [ ] **Step 3: Create `ingestor.py`**

Create `src/lightmes/modules/equipment/ingestor.py`:

```python
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.equipment.tag_service import TagService

logger = logging.getLogger(__name__)


class MachineSignalIngestor:
    """Route a normalized signal value to its domain effect, by signal_type."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tags = TagService(db)
        self.state_machine = WorkstationStateMachine(db)

    def ingest(self, tag: MachineTag, raw_value, work_station_id: int) -> None:
        value = self.tags.apply_transform(tag, raw_value)
        st = tag.signal_type
        if st == "state":
            self._ingest_state(tag, value, work_station_id)
        elif st in ("good_count", "reject_count", "cycle_complete"):
            self._ingest_count(tag, value)
        elif st == "telemetry":
            self._ingest_telemetry(tag, value, work_station_id)
        elif st == "alarm":
            self._ingest_alarm(tag, value, work_station_id)
        else:
            logger.warning("未知 signal_type: %s", st)

    def _ingest_state(self, tag, value, work_station_id):
        if not isinstance(value, str):
            logger.warning("state 信号非字符串，跳过 tag=%s value=%r", tag.name, value)
            return
        self.state_machine.transition(work_station_id, value, source="machine")

    def _ingest_count(self, tag, value):
        try:
            current = int(value)
        except (TypeError, ValueError):
            logger.warning("count 信号非数值，跳过 tag=%s value=%r", tag.name, value)
            return
        last = tag.last_count_value
        if last is not None and current < last:
            logger.info("计数清零（设备重启）tag=%s: %s -> %s", tag.name, last, current)
        tag.last_count_value = current
        self.db.flush()

    def _ingest_telemetry(self, tag, value, work_station_id):
        cur = self.state_machine.current(work_station_id)
        if cur is None:
            return
        meta = dict(cur.metadata or {})
        meta[tag.name] = value
        if tag.unit:
            meta[f"{tag.name}_unit"] = tag.unit
        cur.metadata = meta
        self.db.flush()

    def _ingest_alarm(self, tag, value, work_station_id):
        if not value:
            return
        cur = self.state_machine.current(work_station_id)
        if cur is not None:
            meta = dict(cur.metadata or {})
            meta["alarm"] = value
            cur.metadata = meta
            self.db.flush()
        from lightmes.config import get_settings
        if get_settings().equipment_auto_create_issue_on_fault:
            self._create_alarm_issue(tag, value, work_station_id)

    def _create_alarm_issue(self, tag, value, work_station_id):
        # Task 8 fills this in; placeholder-free stub raises nothing for now.
        logger.info("alarm auto-issue enabled but not yet implemented (tag=%s)", tag.name)


def ingest_topic_signals(db: Session, topic_id: int, parsed_data: dict,
                         work_station_id: int | None) -> None:
    """Extract and ingest all active signal tags for a topic. Never raises."""
    if work_station_id is None:
        return
    tags = TagService(db).list_active_for_topic(topic_id)
    if not tags:
        return
    from lightmes.modules.connectivity.parser import MqttMessageParser

    parser = MqttMessageParser()
    ingestor = MachineSignalIngestor(db)
    for tag in tags:
        try:
            raw = parser.resolve_path(tag.field_path, parsed_data)
            ingestor.ingest(tag, raw, work_station_id)
        except Exception as e:
            logger.warning("信号 ingest 失败 tag=%s: %s", tag.name, e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_ingestor.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/equipment/ingestor.py tests/modules/equipment/test_ingestor.py
git commit -m "feat(equipment): add MachineSignalIngestor routing by signal_type"
```

---

## Task 5: Wire connectivity → equipment (signal bridge)

**Files:**
- Modify: `src/lightmes/modules/connectivity/mqtt_listener/message_service.py`
- Test: `tests/modules/equipment/test_signal_pipeline.py`

**Interfaces:**
- Consumes: `ingest_topic_signals(db, topic_id, parsed_data, work_station_id)` (Task 4).
- Produces: end-to-end MQTT message → `WorkstationState` + `ProductionDowntime` persistence.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_signal_pipeline.py`:

```python
from datetime import datetime, timezone

from lightmes.modules.connectivity.mqtt_listener.message_service import persist_message
from lightmes.modules.connectivity.models import MachineConnection, MachineTopic
from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.masterdata.models import Line, WorkStation


def test_end_to_end_signal_ingest(db_session):
    ensure_system_downtime_reasons(db_session)
    line = Line(code="L_PIPE", name="L_PIPE")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_PIPE", name="WS_PIPE", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()

    conn = MachineConnection(name="C_PIPE", protocol="mqtt", work_station_id=ws.id)
    db_session.add(conn); db_session.flush()
    topic = MachineTopic(machine_connection_id=conn.id, topic_pattern="press/+/state",
                         payload_format="json")
    db_session.add(topic); db_session.flush()
    tag = MachineTag(machine_topic_id=topic.id, name="state", field_path="$.state",
                     signal_type="state", transform={"value_map": {"1": "RUNNING", "2": "FAULT"}})
    db_session.add(tag); db_session.flush()
    db_session.commit()

    result = persist_message(
        connection_id=conn.id, topic="press/1/state",
        payload=b'{"state": "2"}',
        received_at=datetime.now(timezone.utc),
    )
    assert result.status == "ok"

    cur = WorkstationStateMachine(db_session).current(ws.id)
    assert cur is not None
    assert cur.state == "FAULT"
```

Note: `persist_message` uses `database.SessionLocal()`, which `tests/conftest.py` monkeypatches to the test session — so the message and the state transition share the test transaction.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_signal_pipeline.py -v`
Expected: FAIL — `cur is None` (state not ingested yet).

- [ ] **Step 3: Modify `message_service.py` — add signal ingest call**

In `persist_message`, right after the `else: processing_status = "ok"` block (the line after `if mappings:` / `else:`), and **before** `# 5. 入库`, add:

```python
            # 信号语义提取 + ingest（equipment 模块，函数内延迟 import 避免循环）
            try:
                from lightmes.modules.equipment.ingestor import ingest_topic_signals
                ingest_topic_signals(db, matched.id, parsed_data, conn.work_station_id)
            except Exception as e:
                logger.warning("信号 ingest 调度失败: %s", e)
```

Note the file already has a `logger` import? No — add `import logging` and `logger = logging.getLogger(__name__)` at the top of `message_service.py` if absent. Check the existing top-of-file imports: `message_service.py` currently imports `dataclass`, `datetime`, `sqlalchemy select/update`, `lightmes.database`, and models — **no** `logging`. Add it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_signal_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/connectivity/mqtt_listener/message_service.py tests/modules/equipment/test_signal_pipeline.py
git commit -m "feat(equipment): wire connectivity signal bridge into persist_message"
```

---

## Task 6: DowntimeService + OeeService + MonitorService

**Files:**
- Create: `src/lightmes/modules/equipment/downtime_service.py`, `src/lightmes/modules/equipment/oee_service.py`, `src/lightmes/modules/equipment/monitor_service.py`
- Test: `tests/modules/equipment/test_oee_service.py`

**Interfaces:**
- Consumes: `ProductionDowntime`, `DowntimeReason` (Task 1), `Shift` (production), `WorkOrder`/`DefectRecord` (production).
- Produces:
  - `DowntimeService(db)` with `.list_for_station(work_station_id)`, `.assign_reason(downtime_id, reason_id, notes)`
  - `compute_availability(shift_duration_seconds, unplanned_downtime_seconds) -> float`
  - `compute_quality(produced_qty, scrapped_qty) -> float`
  - `compute_oee(availability, quality) -> float`
  - `OeeService(db)` with `.availability_for_station(work_station_id, shift, since, until) -> float`, `.quality_for_work_order(work_order_id) -> float`
  - `MonitorService(db)` with `.current_states() -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_oee_service.py`:

```python
from lightmes.modules.equipment.oee_service import (
    compute_availability, compute_quality, compute_oee,
)


def test_compute_availability():
    # 8h shift, 1h unplanned downtime → (8-1)/8 = 87.5%
    assert abs(compute_availability(8 * 3600, 1 * 3600) - 0.875) < 1e-6


def test_compute_availability_no_downtime():
    assert compute_availability(8 * 3600, 0) == 1.0


def test_compute_availability_zero_shift():
    assert compute_availability(0, 0) == 0.0


def test_compute_quality():
    # 100 produced, 5 scrapped → 95/100 = 95%
    assert abs(compute_quality(100, 5) - 0.95) < 1e-6


def test_compute_quality_zero_produced():
    assert compute_quality(0, 0) == 0.0


def test_compute_oee():
    assert abs(compute_oee(0.875, 0.95) - 0.83125) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_oee_service.py -v`
Expected: FAIL — `No module named 'lightmes.modules.equipment.oee_service'`.

- [ ] **Step 3: Create `downtime_service.py`**

Create `src/lightmes/modules/equipment/downtime_service.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import DowntimeReason, ProductionDowntime
from lightmes.shared.errors import NotFoundError


class DowntimeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_for_station(self, work_station_id: int) -> list[ProductionDowntime]:
        return list(self.db.execute(
            select(ProductionDowntime)
            .where(ProductionDowntime.work_station_id == work_station_id)
            .order_by(ProductionDowntime.started_at.desc())
        ).scalars().all())

    def assign_reason(self, downtime_id: int, reason_id: int | None,
                      notes: str | None = None) -> ProductionDowntime:
        dt = self.db.get(ProductionDowntime, downtime_id)
        if dt is None:
            raise NotFoundError(f"停机记录不存在: {downtime_id}")
        if reason_id is not None:
            reason = self.db.get(DowntimeReason, reason_id)
            if reason is None or not reason.is_active:
                raise NotFoundError(f"停机原因不存在或已停用: {reason_id}")
            dt.downtime_reason_id = reason.id
            dt.is_planned = reason.kind == "planned"
        if notes is not None:
            dt.notes = notes
        self.db.flush()
        return dt
```

- [ ] **Step 4: Create `oee_service.py`**

Create `src/lightmes/modules/equipment/oee_service.py`:

```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import ProductionDowntime
from lightmes.modules.production.models import DefectRecord, Shift, WorkOrder


def compute_availability(shift_duration_seconds: float,
                         unplanned_downtime_seconds: float) -> float:
    if shift_duration_seconds <= 0:
        return 0.0
    return max(0.0, (shift_duration_seconds - unplanned_downtime_seconds) / shift_duration_seconds)


def compute_quality(produced_qty: int, scrapped_qty: int) -> float:
    if produced_qty <= 0:
        return 0.0
    return max(0.0, (produced_qty - scrapped_qty) / produced_qty)


def compute_oee(availability: float, quality: float) -> float:
    return availability * quality


def _shift_duration_seconds(shift: Shift) -> float:
    def to_secs(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 3600 + int(m) * 60
    start = to_secs(shift.start_time)
    end = to_secs(shift.end_time)
    if shift.end_time < shift.start_time:  # cross-midnight
        return (24 * 3600 - start) + end
    return end - start


class OeeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def unplanned_downtime_seconds(self, work_station_id: int,
                                   since: datetime, until: datetime) -> float:
        rows = self.db.execute(
            select(ProductionDowntime).where(
                ProductionDowntime.work_station_id == work_station_id,
                ProductionDowntime.is_planned.is_(False),
                ProductionDowntime.started_at < until,
                (ProductionDowntime.ended_at.is_(None)) |
                (ProductionDowntime.ended_at > since),
            )
        ).scalars().all()
        total = 0.0
        for dt in rows:
            s = dt.started_at if dt.started_at >= since else since
            e = dt.ended_at if (dt.ended_at is not None and dt.ended_at <= until) else until
            if e > s:
                total += (e - s).total_seconds()
        return total

    def availability_for_station(self, work_station_id: int, shift: Shift,
                                 since: datetime, until: datetime) -> float:
        duration = _shift_duration_seconds(shift)
        unplanned = self.unplanned_downtime_seconds(work_station_id, since, until)
        return compute_availability(duration, unplanned)

    def quality_for_work_order(self, work_order_id: int) -> float:
        wo = self.db.get(WorkOrder, work_order_id)
        if wo is None:
            return 0.0
        scrapped = len(self.db.execute(
            select(DefectRecord).where(
                DefectRecord.work_order_id == work_order_id,
                DefectRecord.handling_status == "scrap",
            )
        ).scalars().all())
        return compute_quality(wo.produced_qty, scrapped)
```

Note: `availability_for_station`'s `unplanned` window is intentionally coarse in Phase 1; the pure `compute_availability` function (already tested) carries the OEE math. A precise shift-window integration test is deferred — the spec's §5 test exercises the pure functions.

- [ ] **Step 5: Create `monitor_service.py`**

Create `src/lightmes/modules/equipment/monitor_service.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.equipment.models import WorkstationState


class MonitorService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def current_states(self) -> list[dict]:
        """Current open state per workstation (for the monitor board)."""
        rows = self.db.execute(
            select(WorkstationState).where(WorkstationState.ended_at.is_(None))
        ).scalars().all()
        return [
            {"work_station_id": s.work_station_id, "state": s.state,
             "started_at": s.started_at, "source": s.source,
             "metadata": s.metadata}
            for s in rows
        ]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_oee_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/equipment/downtime_service.py src/lightmes/modules/equipment/oee_service.py src/lightmes/modules/equipment/monitor_service.py tests/modules/equipment/test_oee_service.py
git commit -m "feat(equipment): add downtime/oee/monitor read models"
```

---

## Task 7: Router + templates + register

**Files:**
- Create: `src/lightmes/modules/equipment/router.py`
- Create: 5 templates under `src/lightmes/templates/equipment/`
- Test: `tests/modules/equipment/test_pages.py`

**Interfaces:**
- Consumes: `TagService` (Task 2), `WorkstationStateMachine` (Task 3), `DowntimeService`/`MonitorService` (Task 6).
- Produces: HTML pages `/equipment/monitor`, `/equipment/downtimes`, `/equipment/oee`, `/equipment/tags`, `/equipment/downtime-reasons`; JSON endpoints for CRUD.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_pages.py`:

```python
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app


def _client(db_session, role_name="admin"):
    from lightmes.modules.auth.models import Role, User
    from lightmes.shared.security import hash_password

    role = db_session.query(Role).filter(Role.name == role_name).first()
    if role is None:
        role = Role(name=role_name, display_name=role_name)
        db_session.add(role); db_session.flush()
    u = User(username=f"_eq_{role_name}", password_hash=hash_password("p"),
             display_name="E", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    # log in via session
    with client as c:
        c.post("/login", data={"username": u.username, "password": "p"})
        yield c
    app.dependency_overrides.clear()


def test_monitor_page_ok(db_session):
    for c in _client(db_session):
        resp = c.get("/equipment/monitor")
        assert resp.status_code == 200


def test_downtimes_page_ok(db_session):
    for c in _client(db_session):
        resp = c.get("/equipment/downtimes")
        assert resp.status_code == 200


def test_tags_page_ok(db_session):
    for c in _client(db_session):
        resp = c.get("/equipment/tags")
        assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_pages.py -v`
Expected: FAIL — 404 (routes not registered).

- [ ] **Step 3: Create `router.py`**

Create `src/lightmes/modules/equipment/router.py`:

```python
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import html_role_guard, require_role
from lightmes.modules.equipment.downtime_service import DowntimeService
from lightmes.modules.equipment.monitor_service import MonitorService
from lightmes.modules.equipment.models import DowntimeReason, MachineTag
from lightmes.modules.equipment.schemas import TagCreate, TagUpdate
from lightmes.modules.equipment.tag_service import TagService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/equipment/monitor", response_class=HTMLResponse)
def monitor_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    states = MonitorService(db).current_states()
    return templates.TemplateResponse(
        request, "equipment/monitor.html", {"states": states})


@router.get("/equipment/downtimes", response_class=HTMLResponse)
def downtimes_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    reasons = list(db.execute(
        select(DowntimeReason).where(DowntimeReason.is_active.is_(True))
    ).scalars().all())
    return templates.TemplateResponse(
        request, "equipment/downtimes.html", {"reasons": reasons})


@router.get("/equipment/oee", response_class=HTMLResponse)
def oee_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    return templates.TemplateResponse(request, "equipment/oee.html", {})


@router.get("/equipment/tags", response_class=HTMLResponse)
def tags_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    tags = list(db.execute(select(MachineTag).order_by(MachineTag.id.desc())).scalars().all())
    return templates.TemplateResponse(request, "equipment/tags.html", {"tags": tags})


@router.get("/equipment/downtime-reasons", response_class=HTMLResponse)
def downtime_reasons_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    reasons = list(db.execute(select(DowntimeReason).order_by(DowntimeReason.id)).scalars().all())
    return templates.TemplateResponse(
        request, "equipment/downtime_reasons.html", {"reasons": reasons})


@router.post("/equipment/tags")
def create_tag(data: TagCreate, db: Session = Depends(get_db),
               _=Depends(require_role("admin", "supervisor"))):
    TagService(db).create(data)
    db.commit()
    return RedirectResponse("/equipment/tags", status_code=303)


@router.post("/equipment/tags/{tag_id}/delete")
def delete_tag(tag_id: int, db: Session = Depends(get_db),
               _=Depends(require_role("admin", "supervisor"))):
    TagService(db).delete(tag_id)
    db.commit()
    return RedirectResponse("/equipment/tags", status_code=303)


@router.post("/equipment/downtimes/{downtime_id}/reason")
def assign_downtime_reason(downtime_id: int, reason_id: int = Form(...),
                           notes: str = Form(""), db: Session = Depends(get_db),
                           _=Depends(require_role("admin", "supervisor"))):
    DowntimeService(db).assign_reason(downtime_id, reason_id, notes or None)
    db.commit()
    return RedirectResponse("/equipment/downtimes", status_code=303)
```

- [ ] **Step 4: Create templates**

Create `src/lightmes/templates/equipment/monitor.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>设备监控</h1>
<div class="grid">
  {% for s in states %}
  <div class="card">
    <div class="card__title">工位 #{{ s.work_station_id }}</div>
    <div class="nav-card__desc">状态：<strong>{{ s.state }}</strong> · 来源 {{ s.source }}</div>
  </div>
  {% else %}
  <div class="card"><div class="nav-card__desc">暂无工位状态</div></div>
  {% endfor %}
</div>
{% endblock %}
```

Create `src/lightmes/templates/equipment/downtimes.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>停机记录</h1>
<p>停机原因人工修正入口（列出停机记录 + 原因下拉，Phase 1 简化为原因列表）。</p>
<ul>
  {% for r in reasons %}
  <li>{{ r.code }} — {{ r.name }} ({{ r.kind }})</li>
  {% endfor %}
</ul>
{% endblock %}
```

Create `src/lightmes/templates/equipment/oee.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>OEE 看板</h1>
<p>Phase 1：可用率 × 质量率（性能率待主数据 ideal_cycle_time 就绪后加入）。</p>
{% endblock %}
```

Create `src/lightmes/templates/equipment/tags.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>信号标签</h1>
<table>
  <thead><tr><th>ID</th><th>名称</th><th>signal_type</th><th>field_path</th><th>启用</th></tr></thead>
  <tbody>
    {% for t in tags %}
    <tr><td>{{ t.id }}</td><td>{{ t.name }}</td><td>{{ t.signal_type }}</td><td>{{ t.field_path }}</td><td>{{ t.is_active }}</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

Create `src/lightmes/templates/equipment/downtime_reasons.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>停机原因</h1>
<table>
  <thead><tr><th>ID</th><th>编码</th><th>名称</th><th>类型</th><th>系统</th></tr></thead>
  <tbody>
    {% for r in reasons %}
    <tr><td>{{ r.id }}</td><td>{{ r.code }}</td><td>{{ r.name }}</td><td>{{ r.kind }}</td><td>{{ r.is_system }}</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_pages.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/equipment/router.py src/lightmes/templates/equipment/ tests/modules/equipment/test_pages.py
git commit -m "feat(equipment): add router and monitor/downtime/oee/tags pages"
```

---

## Task 8: Auto-create issue on alarm (gated, default off)

**Files:**
- Modify: `src/lightmes/modules/equipment/ingestor.py`
- Test: `tests/modules/equipment/test_alarm_issue.py`

**Interfaces:**
- Consumes: `IssueService.create_issue` (issue module), `equipment_auto_create_issue_on_fault` setting (Task 1).
- Produces: `_create_alarm_issue(tag, value, work_station_id)` creating an issue with `source="station_andon"`.

**Note on `source` value:** The spec said `source='equipment_alarm'`, but `Issue.source` has a DB check constraint `source IN ('station_andon','defect_linked','manual')` (see `issue/models.py`). Adding a new value would require touching that constraint in both the migration and the model (the test DB is built via `Base.metadata.create_all`, not alembic). `station_andon` ("工位安灯") is the semantically correct existing value for an equipment alarm raised at a workstation, so we reuse it and skip the constraint change. This is a deliberate, documented deviation.

- [ ] **Step 1: Write the failing test**

Create `tests/modules/equipment/test_alarm_issue.py`:

```python
from lightmes.modules.equipment import ensure_system_downtime_reasons
from lightmes.modules.equipment.ingestor import MachineSignalIngestor
from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.issue.models import IssueType
from lightmes.modules.masterdata.models import Line, WorkStation


def _setup(db_session):
    ensure_system_downtime_reasons(db_session)
    line = Line(code="L_AL", name="L_AL")
    db_session.add(line); db_session.flush()
    ws = WorkStation(code="WS_AL", name="WS_AL", line_id=line.id, seq=1)
    db_session.add(ws); db_session.flush()
    it = IssueType(code="equipment", name="设备", severity="major")
    db_session.add(it); db_session.flush()
    return ws


def test_alarm_creates_issue_when_enabled(db_session, monkeypatch):
    from lightmes.config import get_settings

    ws = _setup(db_session)
    tag = MachineTag(machine_topic_id=1, name="alarm", field_path="$.a",
                     signal_type="alarm")
    db_session.add(tag); db_session.flush()

    # get_settings() is @lru_cache; mutate the cached instance so ingestor sees it
    monkeypatch.setattr(get_settings(), "equipment_auto_create_issue_on_fault", True)

    from lightmes.modules.equipment.state_machine import WorkstationStateMachine
    sm = WorkstationStateMachine(db_session)
    from datetime import datetime, timezone
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    MachineSignalIngestor(db_session).ingest(tag, "E-stop triggered", ws.id)

    from lightmes.modules.issue.models import Issue
    from sqlalchemy import select
    issue = db_session.execute(select(Issue)).scalars().first()
    assert issue is not None
    assert issue.source == "station_andon"


def test_alarm_no_issue_when_disabled(db_session):
    ws = _setup(db_session)
    tag = MachineTag(machine_topic_id=1, name="alarm", field_path="$.a",
                     signal_type="alarm")
    db_session.add(tag); db_session.flush()

    from lightmes.modules.equipment.state_machine import WorkstationStateMachine
    sm = WorkstationStateMachine(db_session)
    from datetime import datetime, timezone
    sm.transition(ws.id, "RUNNING", at=datetime(2026, 8, 14, tzinfo=timezone.utc))

    MachineSignalIngestor(db_session).ingest(tag, "E-stop triggered", ws.id)

    from lightmes.modules.issue.models import Issue
    from sqlalchemy import select
    assert db_session.execute(select(Issue)).scalars().first() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/modules/equipment/test_alarm_issue.py -v`
Expected: FAIL — no issue created (stub in Task 4 does nothing).

- [ ] **Step 3: Implement `_create_alarm_issue`**

Replace the stub `_create_alarm_issue` in `ingestor.py` with:

```python
    def _create_alarm_issue(self, tag, value, work_station_id):
        from sqlalchemy import select

        from lightmes.modules.auth.models import User
        from lightmes.modules.issue.repository import IssueTypeRepository
        from lightmes.modules.issue.service import IssueService

        issue_type = IssueTypeRepository(self.db).get_by_code("equipment")
        if issue_type is None:
            logger.warning("缺少 equipment issue type，跳过自动建 issue")
            return
        # reported_by_id 是 NOT NULL FK；机器上报无真人，复用 _system_machine 占位用户
        user = self.db.execute(
            select(User).where(User.username == "_system_machine")
        ).scalar_one_or_none()
        if user is None:
            user = User(
                username="_system_machine", password_hash="!",
                display_name="设备自动上报", is_active=True,
            )
            self.db.add(user)
            self.db.flush()
        IssueService(self.db).create_issue(
            issue_type_id=issue_type.id,
            title=f"设备告警: {tag.name} = {value}",
            description=f"工位 {work_station_id} 告警：{tag.name}={value}",
            source="station_andon",
            work_station_id=work_station_id,
            reported_by_id=user.id,
        )
        self.db.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/modules/equipment/test_alarm_issue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/equipment/ingestor.py tests/modules/equipment/test_alarm_issue.py
git commit -m "feat(equipment): auto-create issue on alarm (gated by setting)"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] MachineTag (address→signal) — Task 1/2
- [x] WorkstationState time-slicing + state machine — Task 1/3
- [x] Auto ProductionDowntime — Task 3
- [x] DowntimeReason + AUTO-* seeds — Task 1
- [x] OEE availability + quality (skip performance) — Task 6
- [x] Monitor board — Task 7
- [x] Manual transition + reason correction — Task 3 (manual source) / Task 7 (assign reason)
- [x] Auto issue on alarm (default off) — Task 8
- [x] Signal bridge connectivity→equipment — Task 5
- [x] machine_connections.work_station_id — Task 1

**Notable gaps (deferred, consistent with spec out-of-scope):**
- Performance rate (OEE-P), EAM (ledger/inspection/maintenance), WebSocket, write-back, multi-equipment aggregation — all spec out-of-scope.
- Precise shift-window OEE integration test — the pure functions are tested; the windowing helper is coarse in Phase 1.

**Type consistency:** `WorkstationStateMachine.transition` / `.current`, `MachineSignalIngestor.ingest`, `TagService.apply_transform` / `.list_active_for_topic`, `ingest_topic_signals(db, topic_id, parsed_data, work_station_id)` signatures are stable across tasks.

**Documented deviations from spec (decided during planning):**
1. Signal forwarding uses a synchronous call + function-level lazy import instead of an event bus (transactional atomicity; see Architecture note).
2. `Issue.source` uses `"station_andon"` instead of `"equipment_alarm"` (existing DB check constraint; see Task 8 note).
