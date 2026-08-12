# 数采-2：解析引擎 + Action 系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MQTT 数采消息添加解析能力（JSON/plain/csv/hex + JSONPath + 条件表达式）+ 6 种业务 action（log_event / update_wo_qty / set_wo_status / update_sn_status / create_defect / webhook_forward）+ TopicMapping CRUD admin UI。

**Architecture:** 新增 `topic_mappings` 表 + `MachineMessage` 加 3 列（parsed_data / actions_triggered / processing_error）。新增 `MqttMessageParser`（纯函数）+ `ActionExecutor`（6 handler dispatch）。增强 `persist_message` 集成解析+执行流程。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2+HTMX, httpx (webhook_forward), PostgreSQL, pytest

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（127.0.0.1 not localhost）
- Tests use `db_session` fixture（SAVEPOINT 隔离）
- Service raises `DomainError` 子类 from `lightmes.shared.errors`
- 文案 Chinese for all user-facing strings
- 最新 migration ID = `d0f6b2c75a8e`（数采-1），Task 1 的 down_revision
- `action_type` 只接受 6 个值：log_event / update_work_order_produced_qty / set_work_order_status / update_serial_unit_status / create_defect / webhook_forward
- Action 各自内部 commit；persist_message 只 commit MachineMessage 行
- persist_message 从 `lightmes.modules.connectivity.mqtt_listener.message_service` 模块
- Admin UI 用标准 LightMES CSS（.card/.form-row/.data-table），不走 planner.css
- Admin routes 用 `Depends(require_role("admin", "supervisor"))`
- 3-arg `TemplateResponse(request, name, context)` form（2-arg crashes per prior tasks）
- webhook_forward 用 `httpx.AsyncClient` + 10s timeout

---

### Task 1: Migration + TopicMapping Model + MachineMessage New Fields

**Files:**
- Modify: `src/lightmes/modules/connectivity/models.py`（加 TopicMapping + MachineMessage 加 3 列）
- Create: `src/lightmes/migrations/versions/e1a7c3d86b9f_add_topic_mappings_and_message_fields.py`
- Modify: `tests/conftest.py`（确保 TopicMapping 被注册，已在数采-1 导入了 connectivity.models）
- Test: `tests/modules/connectivity/test_models.py`（扩展）

**Interfaces:**
- Consumes: 数采-1 的 MachineTopic / MachineMessage
- Produces: `TopicMapping` model + MachineMessage.parsed_data/actions_triggered/processing_error fields

- [ ] **Step 1: 加 TopicMapping 到 models.py**

在 `src/lightmes/modules/connectivity/models.py` 末尾追加：

```python
class TopicMapping(Base, TimestampMixin):
    __tablename__ = "topic_mappings"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('log_event', 'update_work_order_produced_qty', "
            "'set_work_order_status', 'update_serial_unit_status', "
            "'create_defect', 'webhook_forward')",
            name="ck_topic_mappings_action_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_topic_id: Mapped[int] = mapped_column(
        ForeignKey("machine_topics.id", ondelete="CASCADE"), index=True)
    description: Mapped[str | None] = mapped_column(String(255), default=None)
    field_path: Mapped[str | None] = mapped_column(String(255), default=None)
    action_type: Mapped[str] = mapped_column(String(30))
    action_params: Mapped[dict | None] = mapped_column(JSON, default=None)
    condition_expr: Mapped[str | None] = mapped_column(String(255), default=None)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(default=True)
```

Ensure `CheckConstraint` and `Text` are imported at top:

```python
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, CheckConstraint, func, JSON
```

- [ ] **Step 2: 加 3 列到 MachineMessage**

在 `MachineMessage` 类的 `processing_status` 字段后追加：

```python
    parsed_data: Mapped[dict | None] = mapped_column(JSON, default=None)
    actions_triggered: Mapped[list | None] = mapped_column(JSON, default=None)
    processing_error: Mapped[str | None] = mapped_column(Text, default=None)
```

- [ ] **Step 3: 创建 Migration**

创建 `src/lightmes/migrations/versions/e1a7c3d86b9f_add_topic_mappings_and_message_fields.py`：

```python
"""add_topic_mappings_and_message_fields

Revision ID: e1a7c3d86b9f
Revises: d0f6b2c75a8e
Create Date: 2026-08-13 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e1a7c3d86b9f'
down_revision = 'd0f6b2c75a8e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('topic_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_topic_id', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('field_path', sa.String(length=255), nullable=True),
        sa.Column('action_type', sa.String(length=30), nullable=False),
        sa.Column('action_params', sa.JSON(), nullable=True),
        sa.Column('condition_expr', sa.String(length=255), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_topic_id'], ['machine_topics.id'], ondelete='CASCADE'),
        sa.CheckConstraint(
            "action_type IN ('log_event', 'update_work_order_produced_qty', "
            "'set_work_order_status', 'update_serial_unit_status', "
            "'create_defect', 'webhook_forward')",
            name='ck_topic_mappings_action_type'),
    )
    op.create_index('ix_topic_mappings_machine_topic_id',
                    'topic_mappings', ['machine_topic_id'])
    op.add_column('machine_messages', sa.Column('parsed_data', sa.JSON(), nullable=True))
    op.add_column('machine_messages', sa.Column('actions_triggered', sa.JSON(), nullable=True))
    op.add_column('machine_messages', sa.Column('processing_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('machine_messages', 'processing_error')
    op.drop_column('machine_messages', 'actions_triggered')
    op.drop_column('machine_messages', 'parsed_data')
    op.drop_index('ix_topic_mappings_machine_topic_id', table_name='topic_mappings')
    op.drop_table('topic_mappings')
```

- [ ] **Step 4: 加模型测试**

在 `tests/modules/connectivity/test_models.py` 末尾追加：

```python
from lightmes.modules.connectivity.models import TopicMapping


def test_topic_mapping_basic_fields(db_session):
    from lightmes.modules.connectivity.models import MachineConnection, MachineTopic
    c = MachineConnection(name="tm-test")
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="x", payload_format="json")
    db_session.add(t); db_session.flush()
    m = TopicMapping(
        machine_topic_id=t.id, action_type="log_event",
        action_params={"key": "val"}, priority=50)
    db_session.add(m); db_session.flush()
    assert m.id is not None
    assert m.action_type == "log_event"
    assert m.priority == 50
    assert m.is_active is True


def test_machine_message_new_fields(db_session):
    from datetime import datetime, timezone
    from lightmes.modules.connectivity.models import MachineConnection
    c = MachineConnection(name="nm-test")
    db_session.add(c); db_session.flush()
    msg = MachineMessage(
        machine_connection_id=c.id, topic="t", raw_payload="p",
        received_at=datetime.now(timezone.utc),
        parsed_data={"count": 1},
        actions_triggered=[{"status": "ok"}],
        processing_error=None)
    db_session.add(msg); db_session.flush()
    assert msg.parsed_data == {"count": 1}
    assert msg.actions_triggered == [{"status": "ok"}]
```

- [ ] **Step 5: 运行 migration + 测试**

```bash
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
uv run pytest tests/modules/connectivity/test_models.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/connectivity/models.py \
        src/lightmes/migrations/versions/e1a7c3d86b9f_add_topic_mappings_and_message_fields.py \
        tests/modules/connectivity/test_models.py
git commit -m "feat(connectivity): TopicMapping model + MachineMessage parsed_data/actions/error fields"
```

---

### Task 2: MqttMessageParser (parse + resolve_path + evaluate_condition)

**Files:**
- Create: `src/lightmes/modules/connectivity/parser.py`
- Test: `tests/modules/connectivity/test_parser.py`

**Interfaces:**
- Consumes: 无
- Produces: `MqttMessageParser` class with `parse(payload, format) -> dict`, `resolve_path(path, data) -> any`, `evaluate_condition(expr, value) -> bool`

- [ ] **Step 1: 写测试**

创建 `tests/modules/connectivity/test_parser.py`（13 tests covering parse 4 formats + invalid JSON + resolve_path nested/array/literal/none + conditions gt/eq/contains/none）:

```python
import pytest
from lightmes.modules.connectivity.parser import MqttMessageParser


@pytest.fixture
def p():
    return MqttMessageParser()


# --- parse ---

def test_parse_json(p):
    result = p.parse('{"count": 5, "status": "ok"}', "json")
    assert result["count"] == 5
    assert result["status"] == "ok"


def test_parse_plain(p):
    result = p.parse("hello world", "plain")
    assert result == {"value": "hello world"}


def test_parse_csv(p):
    result = p.parse("a,b,c\n1,2,3", "csv")
    assert result["rows"] == [["a", "b", "c"], ["1", "2", "3"]]


def test_parse_hex(p):
    result = p.parse("48656c6c6f", "hex")
    assert result["hex"] == "48656c6c6f"
    assert result["bytes"] == [0x48, 0x65, 0x6c, 0x6c, 0x6f]


def test_parse_invalid_json(p):
    result = p.parse("{bad json", "json")
    assert "_raw" in result
    assert "_error" in result


def test_parse_unknown_format(p):
    result = p.parse("data", "xml")
    assert "_raw" in result


# --- resolve_path ---

def test_resolve_path_nested(p):
    data = {"a": {"b": {"c": 42}}}
    assert p.resolve_path("$.a.b.c", data) == 42


def test_resolve_path_array(p):
    data = {"arr": [10, 20, 30]}
    assert p.resolve_path("$.arr.0", data) == 10
    assert p.resolve_path("$.arr.2", data) == 30


def test_resolve_path_literal(p):
    assert p.resolve_path("literal_value", {"a": 1}) == "literal_value"


def test_resolve_path_none(p):
    data = {"x": 1}
    assert p.resolve_path(None, data) == data


def test_resolve_path_missing(p):
    data = {"a": 1}
    assert p.resolve_path("$.b", data) is None


# --- evaluate_condition ---

def test_condition_gt(p):
    assert p.evaluate_condition("value > 5", 10) is True
    assert p.evaluate_condition("value > 5", 3) is False


def test_condition_eq(p):
    assert p.evaluate_condition("status == active", "active") is True
    assert p.evaluate_condition("status == active", "inactive") is False


def test_condition_contains(p):
    assert p.evaluate_condition("code contains ERR", "ERROR_001") is True
    assert p.evaluate_condition("code contains ERR", "OK") is False


def test_condition_none_always_true(p):
    assert p.evaluate_condition(None, "anything") is True


def test_condition_unparseable(p):
    assert p.evaluate_condition("garbage expression", 1) is True
```

- [ ] **Step 2: 运行 RED**

Run: `uv run pytest tests/modules/connectivity/test_parser.py -v`
Expected: ImportError.

- [ ] **Step 3: 实现 parser.py**

创建 `src/lightmes/modules/connectivity/parser.py`：

```python
"""MQTT message parser — format parsing, JSONPath field resolution, condition evaluation.

Adapted from OpenMES MqttMessageParser with Python idioms.
"""
import csv
import io
import json
import re


class MqttMessageParser:
    """Parse raw MQTT payloads and resolve field paths / conditions."""

    def parse(self, payload: str, fmt: str) -> dict:
        """Parse payload by declared format. Returns dict on success, {'_raw': ..., '_error': ...} on failure."""
        try:
            if fmt == "json":
                decoded = json.loads(payload)
                return decoded if isinstance(decoded, dict) else {"value": decoded}
            elif fmt == "plain":
                return {"value": payload}
            elif fmt == "csv":
                reader = csv.reader(io.StringIO(payload.strip()))
                rows = [row for row in reader if row]
                return {"rows": rows}
            elif fmt == "hex":
                cleaned = payload.strip().replace(" ", "")
                byte_vals = [int(cleaned[i:i+2], 16) for i in range(0, len(cleaned), 2)]
                return {"hex": cleaned, "bytes": byte_vals}
            else:
                return {"_raw": payload, "_error": f"Unknown format: {fmt}"}
        except Exception as e:
            return {"_raw": payload, "_error": str(e)}

    def resolve_path(self, path: str | None, data: dict) -> any:
        """Resolve a JSONPath-like path from parsed data.

        "$.field" → data["field"]
        "$.nested.field" → data["nested"]["field"]
        "$.arr.0" → data["arr"][0]
        No "$" prefix → literal value
        None → entire data dict
        """
        if path is None:
            return data
        if not path.startswith("$"):
            return path
        if path == "$":
            return data
        keys = path[2:].split(".")  # strip "$."
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list):
                try:
                    idx = int(key)
                    value = value[idx]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return value

    def evaluate_condition(self, expr: str | None, resolved_value) -> bool:
        """Evaluate a simple condition expression against a resolved value.

        Supported: value == X, value != X, value > X, value >= X, value < X, value <= X, value contains X
        None → True (always pass). Unparseable → True (fail-safe).
        """
        if expr is None or not expr.strip():
            return True
        # Match "value <op> <literal>"
        m = re.match(r"^value\s*(==|!=|>=|<=|>|<|contains)\s*(.+)$", expr.strip())
        if not m:
            return True  # fail-safe
        op, literal = m.group(1), m.group(2).strip()
        # Coerce literal
        if literal.lower() == "true":
            lit = True
        elif literal.lower() == "false":
            lit = False
        elif literal.lower() == "null":
            lit = None
        else:
            try:
                lit = int(literal)
            except ValueError:
                try:
                    lit = float(literal)
                except ValueError:
                    lit = literal  # string
        try:
            if op == "==":
                return resolved_value == lit
            elif op == "!=":
                return resolved_value != lit
            elif op == ">":
                return resolved_value > lit
            elif op == ">=":
                return resolved_value >= lit
            elif op == "<":
                return resolved_value < lit
            elif op == "<=":
                return resolved_value <= lit
            elif op == "contains":
                return str(lit) in str(resolved_value)
        except TypeError:
            return False  # type mismatch
        return True
```

- [ ] **Step 4: 运行 GREEN**

Run: `uv run pytest tests/modules/connectivity/test_parser.py -v`
Expected: 17 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/connectivity/parser.py tests/modules/connectivity/test_parser.py
git commit -m "feat(connectivity): MqttMessageParser (parse + resolve_path + evaluate_condition)"
```

---

### Task 3: ActionExecutor + 6 Action Handlers

**Files:**
- Create: `src/lightmes/modules/connectivity/action_executor.py`
- Test: `tests/modules/connectivity/test_action_executor.py`

**Interfaces:**
- Consumes: Task 1 的 TopicMapping model + Task 2 的 MqttMessageParser + 已有 service 层（ProductionService / DefectService / SerialUnitRepository）
- Produces: `ActionExecutor` class with `execute_all(mappings, parsed_data) -> list[dict]` + `execute_single(mapping, parsed_data) -> dict`

- [ ] **Step 1: 写测试**

创建 `tests/modules/connectivity/test_action_executor.py`:

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from lightmes.modules.connectivity.action_executor import ActionExecutor
from lightmes.modules.connectivity.parser import MqttMessageParser
from lightmes.modules.connectivity.models import TopicMapping


def _mapping(action_type, params=None, field_path=None, condition=None, priority=100):
    return TopicMapping(
        id=1, machine_topic_id=1, action_type=action_type,
        action_params=params or {}, field_path=field_path,
        condition_expr=condition, priority=priority, is_active=True)


def test_log_event(db_session):
    ex = ActionExecutor(db_session)
    result = ex.execute_single(_mapping("log_event"), {"x": 1})
    assert result["status"] == "ok"


def test_condition_not_met_skipped(db_session):
    ex = ActionExecutor(db_session)
    m = _mapping("log_event", condition="value > 100")
    result = ex.execute_single(m, {})
    assert result["status"] == "skipped"


def test_unknown_action_error(db_session):
    ex = ActionExecutor(db_session)
    result = ex.execute_single(_mapping("bogus_action"), {})
    assert result["status"] == "error"


def test_update_wo_qty_increment(db_session):
    """Increment WO produced_qty by resolved field value."""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AEWP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AEWL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AEWW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AEWR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AEWRR", name="r", pattern="AEW{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AEWWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=100, sn_rule_id=rule.id))
    db_session.flush()

    ex = ActionExecutor(db_session)
    m = _mapping("update_work_order_produced_qty",
                 params={"work_order_code_path": "$.order_no", "qty_increment": True},
                 field_path="$.qty")
    result = ex.execute_single(m, {"order_no": "AEWWO", "qty": 5})
    assert result["status"] == "ok"
    db_session.expire_all()
    wo = db_session.get(type(wo), wo.id)
    assert wo.produced_qty == 5


def test_set_wo_status(db_session):
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AESW", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AESL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AESW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AESR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AESRR", name="r", pattern="AES{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AESWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    db_session.flush()
    ex = ActionExecutor(db_session)
    m = _mapping("set_work_order_status",
                 params={"work_order_code_path": "$.order_no", "status": "released"})
    result = ex.execute_single(m, {"order_no": "AESWO"})
    assert result["status"] == "ok"
    db_session.expire_all()
    assert db_session.get(type(wo), wo.id).status == "released"


def test_update_sn_status(db_session):
    from lightmes.modules.production.models import SerialUnit
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AEUP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AEUL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AEUW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AEUR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AEURR", name="r", pattern="AEU{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AEUWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AEUSN1", work_order_id=wo.id, product_id=p.id, status="in_process")
    db_session.add(su); db_session.flush()
    ex = ActionExecutor(db_session)
    m = _mapping("update_serial_unit_status",
                 params={"sn_path": "$.sn", "status": "scrapped"})
    result = ex.execute_single(m, {"sn": "AEUSN1"})
    assert result["status"] == "ok"
    db_session.expire_all()
    assert db_session.get(SerialUnit, su.id).status == "scrapped"


def test_create_defect(db_session):
    from lightmes.modules.production.models import SerialUnit, DefectType, DefectRecord
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AEDP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AEDL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AEDW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AEDR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AEDRR", name="r", pattern="AED{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AEDWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AEDSN1", work_order_id=wo.id, product_id=p.id, status="in_process")
    db_session.add(su)
    dt = DefectType(code="AUTO_TEST", name="自动测试缺陷", category="质量",
                    severity="minor", is_active=True)
    db_session.add(dt); db_session.flush()
    ex = ActionExecutor(db_session)
    m = _mapping("create_defect",
                 params={"sn_path": "$.sn", "defect_type_code": "AUTO_TEST"})
    result = ex.execute_single(m, {"sn": "AEDSN1"})
    assert result["status"] == "ok"
    db_session.expire_all()
    defects = db_session.query(DefectRecord).filter(DefectRecord.serial_unit_id == su.id).all()
    assert len(defects) == 1


def test_webhook_forward_mocked(db_session):
    ex = ActionExecutor(db_session)
    m = _mapping("webhook_forward",
                 params={"url": "https://example.com/hook", "method": "POST"})
    with patch("lightmes.modules.connectivity.action_executor.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_httpx.AsyncClient.return_value = mock_client
        result = ex.execute_single(m, {"event": "cycle_complete"})
    assert result["status"] == "ok"


def test_execute_all_multiple(db_session):
    """Multiple mappings execute in priority order, independent failures."""
    ex = ActionExecutor(db_session)
    mappings = [
        _mapping("log_event", priority=100),
        _mapping("bogus_action", priority=200),
    ]
    results = ex.execute_all(mappings, {"x": 1})
    assert len(results) == 2
    assert results[0]["status"] == "ok"       # log_event (priority 100 first)
    assert results[1]["status"] == "error"    # bogus_action
```

- [ ] **Step 2: 运行 RED**

Run: `uv run pytest tests/modules/connectivity/test_action_executor.py -v`
Expected: ImportError.

- [ ] **Step 3: 实现 ActionExecutor**

创建 `src/lightmes/modules/connectivity/action_executor.py`:

```python
"""ActionExecutor — dispatches TopicMapping actions against parsed MQTT data.

Each action handler is independent: failures are caught and recorded per-mapping.
Actions commit their own writes; ActionExecutor does not manage transactions.
"""
import asyncio
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.connectivity.parser import MqttMessageParser

logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self, db: Session):
        self.db = db
        self.parser = MqttMessageParser()

    def execute_all(self, mappings: list, parsed_data: dict) -> list[dict]:
        """Execute all mappings (already sorted by priority). Returns result list."""
        results = []
        for mapping in sorted(mappings, key=lambda m: m.priority):
            results.append(self.execute_single(mapping, parsed_data))
        return results

    def execute_single(self, mapping, parsed_data: dict) -> dict:
        result = {
            "mapping_id": mapping.id,
            "action_type": mapping.action_type,
            "status": "skipped",
            "message": None,
        }
        try:
            field_value = self.parser.resolve_path(mapping.field_path, parsed_data)
            if not self.parser.evaluate_condition(mapping.condition_expr, field_value):
                result["message"] = "Condition not met"
                return result
            params = mapping.action_params or {}
            outcome = self._dispatch(mapping.action_type, params, parsed_data, field_value)
            result["status"] = "ok"
            result["message"] = json.dumps(outcome) if outcome else None
        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)[:500]
            logger.warning(f"Action {mapping.action_type} failed: {e}")
        return result

    def _dispatch(self, action_type: str, params: dict, data: dict, field_value):
        handlers = {
            "log_event": self._log_event,
            "update_work_order_produced_qty": self._update_wo_qty,
            "set_work_order_status": self._set_wo_status,
            "update_serial_unit_status": self._update_sn_status,
            "create_defect": self._create_defect,
            "webhook_forward": self._webhook_forward,
        }
        handler = handlers.get(action_type)
        if handler is None:
            raise ValueError(f"未知 action 类型: {action_type}")
        return handler(params, data, field_value)

    def _resolve_param(self, params: dict, key: str, data: dict):
        """先查 {key}_path（动态解析），再查 {key}（静态值）。"""
        path = params.get(f"{key}_path")
        if path:
            return self.parser.resolve_path(path, data)
        return params.get(key)

    # ── Action handlers ──────────────────────────────────────────────

    def _log_event(self, params, data, field_value):
        return {"logged": True}

    def _update_wo_qty(self, params, data, field_value):
        from lightmes.modules.production.models import WorkOrder
        order_code = self._resolve_param(params, "work_order_code", data)
        qty = self._resolve_param(params, "qty", data)
        if qty is None:
            qty = field_value
        increment = bool(params.get("qty_increment", False))
        if order_code is None:
            raise ValueError("缺少 work_order_code 或 work_order_code_path")
        wo = self.db.execute(
            select(WorkOrder).where(WorkOrder.code == order_code)
        ).scalar_one_or_none()
        if wo is None:
            raise ValueError(f"工单不存在: {order_code}")
        qty_num = int(qty) if qty is not None else 0
        if increment:
            wo.produced_qty = (wo.produced_qty or 0) + qty_num
        else:
            wo.produced_qty = qty_num
        self.db.commit()
        return {"work_order": wo.code, "produced_qty": wo.produced_qty, "increment": increment}

    def _set_wo_status(self, params, data, field_value):
        from lightmes.modules.production.models import WorkOrder
        order_code = self._resolve_param(params, "work_order_code", data)
        status = self._resolve_param(params, "status", data)
        if order_code is None or status is None:
            raise ValueError("缺少 work_order_code 或 status")
        wo = self.db.execute(
            select(WorkOrder).where(WorkOrder.code == order_code)
        ).scalar_one_or_none()
        if wo is None:
            raise ValueError(f"工单不存在: {order_code}")
        wo.status = status
        self.db.commit()
        return {"work_order": wo.code, "status": status}

    def _update_sn_status(self, params, data, field_value):
        from lightmes.modules.production.models import SerialUnit
        sn = self._resolve_param(params, "sn", data)
        status = self._resolve_param(params, "status", data)
        if sn is None or status is None:
            raise ValueError("缺少 sn 或 status")
        su = self.db.execute(
            select(SerialUnit).where(SerialUnit.sn == sn)
        ).scalar_one_or_none()
        if su is None:
            raise ValueError(f"SN 不存在: {sn}")
        su.status = status
        self.db.commit()
        return {"sn": sn, "status": status}

    def _create_defect(self, params, data, field_value):
        from lightmes.modules.production.defect_service import DefectService
        sn = self._resolve_param(params, "sn", data)
        defect_type_code = self._resolve_param(params, "defect_type_code", data)
        remark = self._resolve_param(params, "remark", data) or "机器自动报告"
        if sn is None or defect_type_code is None:
            raise ValueError("缺少 sn 或 defect_type_code")
        from lightmes.modules.connectivity.models import MachineConnection
        # DefectService.log_defect needs discovered_by; use first admin user or system
        from lightmes.modules.auth.models import User
        admin = self.db.execute(
            select(User).where(User.is_active.is_(True)).limit(1)
        ).scalar_one_or_none()
        svc = DefectService(self.db)
        from lightmes.modules.production.models import DefectType
        dt = self.db.execute(
            select(DefectType).where(DefectType.code == defect_type_code)
        ).scalar_one_or_none()
        if dt is None:
            raise ValueError(f"缺陷类型不存在: {defect_type_code}")
        record = svc.log_defect(
            defect_type_id=dt.id, sn=sn,
            discovered_by=admin.id if admin else None,
            remark=remark)
        self.db.commit()
        return {"defect_id": record.id, "sn": sn}

    def _webhook_forward(self, params, data, field_value):
        url = params.get("url")
        method = params.get("method", "POST")
        if not url:
            raise ValueError("缺少 webhook url")
        # Sync wrapper for async httpx (called from sync execute_single)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context (listener); can't use run_until_complete
                # Fallback: use httpx sync client instead
                import httpx as _httpx
                resp = _httpx.request(method, url, json=data, timeout=10.0)
            else:
                raise RuntimeError("no running loop")
        except RuntimeError:
            import httpx as _httpx
            resp = _httpx.request(method, url, json=data, timeout=10.0)
        if resp.status_code >= 400:
            raise ValueError(f"Webhook 返回 {resp.status_code}: {resp.text[:200]}")
        return {"status_code": resp.status_code, "url": url}
```

- [ ] **Step 4: 运行 GREEN**

Run: `uv run pytest tests/modules/connectivity/test_action_executor.py -v`
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/connectivity/action_executor.py \
        tests/modules/connectivity/test_action_executor.py
git commit -m "feat(connectivity): ActionExecutor with 6 action handlers (log/wo_qty/wo_status/sn_status/defect/webhook)"
```

---

### Task 4: persist_message Enhancement

**Files:**
- Modify: `src/lightmes/modules/connectivity/mqtt_listener/message_service.py`
- Test: `tests/modules/connectivity/test_message_service.py`（扩展）

**Interfaces:**
- Consumes: Task 2 parser + Task 3 ActionExecutor + Task 1 TopicMapping
- Produces: enhanced `persist_message` that parses + executes actions + stores results

- [ ] **Step 1: 写测试**

在 `tests/modules/connectivity/test_message_service.py` 末尾追加：

```python
def test_persist_message_with_mapping_log_event(db_session):
    """Message with active mapping → parsed_data + actions_triggered stored."""
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineTopic, TopicMapping, MachineMessage as MM)
    c = MachineConnection(name="pe-log", is_active=True)
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="test/topic",
                     payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    m = TopicMapping(machine_topic_id=t.id, action_type="log_event",
                     field_path="$.event", priority=100, is_active=True)
    db_session.add(m); db_session.commit()

    result = persist_message(c.id, "test/topic", b'{"event": "cycle_done"}',
                             datetime.now(timezone.utc))
    assert result.status == "ok"
    # Verify stored message has parsed_data + actions_triggered
    from lightmes.database import SessionLocal
    db = SessionLocal()
    try:
        msg = db.execute(
            __import__("sqlalchemy").select(MM).where(
                MM.machine_connection_id == c.id)
        ).scalars().first()
        assert msg.parsed_data == {"event": "cycle_done"}
        assert msg.actions_triggered is not None
        assert len(msg.actions_triggered) == 1
        assert msg.actions_triggered[0]["status"] == "ok"
    finally:
        db.close()


def test_persist_message_condition_not_met(db_session):
    """Mapping with condition not met → status=skipped."""
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineTopic, TopicMapping, MachineMessage as MM)
    c = MachineConnection(name="pe-cond", is_active=True)
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="test/c",
                     payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    m = TopicMapping(machine_topic_id=t.id, action_type="log_event",
                     field_path="$.count", condition_expr="value > 100",
                     priority=100, is_active=True)
    db_session.add(m); db_session.commit()

    result = persist_message(c.id, "test/c", b'{"count": 5}',
                             datetime.now(timezone.utc))
    assert result.status == "skipped"


def test_persist_message_action_error_continues(db_session):
    """One mapping errors → recorded, others continue. Overall status=ok if any succeed."""
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineTopic, TopicMapping)
    c = MachineConnection(name="pe-err", is_active=True)
    db_session.add(c); db_session.flush()
    t = MachineTopic(machine_connection_id=c.id, topic_pattern="test/e",
                     payload_format="json", is_active=True)
    db_session.add(t); db_session.flush()
    # bad mapping (references nonexistent WO)
    db_session.add(TopicMapping(
        machine_topic_id=t.id, action_type="update_work_order_produced_qty",
        action_params={"work_order_code": "NOSUCH", "qty_increment": True},
        field_path="$.qty", priority=100, is_active=True))
    # good mapping
    db_session.add(TopicMapping(
        machine_topic_id=t.id, action_type="log_event",
        priority=200, is_active=True))
    db_session.commit()

    result = persist_message(c.id, "test/e", b'{"qty": 1}',
                             datetime.now(timezone.utc))
    # At least one ok → overall ok
    assert result.status == "ok"
```

- [ ] **Step 2: 运行 RED**

Run: `uv run pytest tests/modules/connectivity/test_message_service.py -v -k "mapping or condition or error_continues"`
Expected: FAIL (persist_message doesn't yet parse/execute).

- [ ] **Step 3: 增强 persist_message**

修改 `src/lightmes/modules/connectivity/mqtt_listener/message_service.py`，在 `persist_message` 函数中，在 matched topic 之后、入库之前，加入解析 + action 执行。替换 `persist_message` 函数体为：

```python
def persist_message(
    connection_id: int,
    topic: str,
    payload: bytes,
    received_at: datetime,
) -> MessagePersistResult:
    """Persist one received MQTT message with parsing + action execution."""
    from sqlalchemy import select as _select
    from lightmes.modules.connectivity.models import (
        MachineConnection, MachineMessage, MachineTopic, TopicMapping,
    )
    from lightmes.modules.connectivity.parser import MqttMessageParser
    from lightmes.modules.connectivity.topic_match import matches_topic

    db = SessionLocal()
    try:
        conn = db.get(MachineConnection, connection_id)
        if conn is None:
            return MessagePersistResult(status="error", error=f"connection 不存在: {connection_id}")

        # 1. Match topic
        topics = list(db.execute(
            _select(MachineTopic).where(
                MachineTopic.machine_connection_id == connection_id,
                MachineTopic.is_active.is_(True),
            )
        ).scalars().all())
        matched = next((t for t in topics if matches_topic(t.topic_pattern, topic)), None)

        # 2. Parse payload if matched
        parsed_data = None
        actions_triggered = None
        processing_status = "skipped"
        processing_error = None

        if matched:
            parser = MqttMessageParser()
            payload_str = payload.decode("utf-8", errors="replace").replace("\x00", "")
            parsed_data = parser.parse(payload_str, matched.payload_format)

            # 3. Query active mappings
            mappings = list(db.execute(
                _select(TopicMapping).where(
                    TopicMapping.machine_topic_id == matched.id,
                    TopicMapping.is_active.is_(True),
                ).order_by(TopicMapping.priority)
            ).scalars().all())

            if mappings:
                from lightmes.modules.connectivity.action_executor import ActionExecutor
                executor = ActionExecutor(db)
                actions_triggered = executor.execute_all(mappings, parsed_data)
                has_error = any(r["status"] == "error" for r in actions_triggered)
                has_ok = any(r["status"] == "ok" for r in actions_triggered)
                processing_status = "error" if has_error and not has_ok else ("ok" if has_ok else "skipped")
                if has_error:
                    processing_error = "; ".join(
                        r["message"] or "" for r in actions_triggered if r["status"] == "error"
                    )[:500]
            else:
                processing_status = "ok"

        # 4. Store message
        msg = MachineMessage(
            machine_connection_id=connection_id,
            topic=topic,
            raw_payload=payload.decode("utf-8", errors="replace").replace("\x00", ""),
            matched_topic_id=matched.id if matched else None,
            parsed_data=parsed_data if parsed_data else None,
            actions_triggered=actions_triggered,
            processing_status=processing_status,
            processing_error=processing_error,
            received_at=received_at,
        )
        db.add(msg)
        db.execute(
            __import__("sqlalchemy").update(MachineConnection)
            .where(MachineConnection.id == connection_id)
            .values(messages_received=MachineConnection.messages_received + 1)
        )
        db.commit()
        return MessagePersistResult(
            status=processing_status,
            matched_topic_id=matched.id if matched else None,
        )
    except Exception as e:
        db.rollback()
        return MessagePersistResult(status="error", error=str(e))
    finally:
        db.close()
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/modules/connectivity/test_message_service.py -v`
Expected: all PASS (数采-1's 5 + 3 new = 8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightmes/modules/connectivity/mqtt_listener/message_service.py \
        tests/modules/connectivity/test_message_service.py
git commit -m "feat(connectivity): persist_message enhanced with parsing + action execution"
```

---

### Task 5: ConnectivityService TopicMapping CRUD + Admin UI Mapping CRUD + Detail Enhancement

**Files:**
- Modify: `src/lightmes/modules/connectivity/service.py`（add mapping methods）
- Modify: `src/lightmes/modules/connectivity/repository.py`（add TopicMappingRepository）
- Modify: `src/lightmes/modules/connectivity/router.py`（add mapping routes）
- Modify: `src/lightmes/templates/connectivity/connection_detail.html`（mapping UI）
- Test: `tests/modules/connectivity/test_router.py`（扩展）

**This task combines service + UI since they're tightly coupled and the service methods are simple CRUD.**

- [ ] **Step 1: 加 TopicMappingRepository**

在 `src/lightmes/modules/connectivity/repository.py` 末尾追加：

```python
from lightmes.modules.connectivity.models import TopicMapping


class TopicMappingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, m: TopicMapping) -> TopicMapping:
        self.db.add(m); self.db.flush()
        return m

    def get(self, mapping_id: int) -> TopicMapping | None:
        return self.db.get(TopicMapping, mapping_id)

    def list_for_topic(self, topic_id: int) -> list[TopicMapping]:
        return list(self.db.execute(
            select(TopicMapping).where(TopicMapping.machine_topic_id == topic_id)
            .order_by(TopicMapping.priority)
        ).scalars().all())

    def delete(self, mapping_id: int) -> None:
        m = self.get(mapping_id)
        if m is not None:
            self.db.delete(m)
            self.db.flush()
```

- [ ] **Step 2: 加 Service 方法**

在 `ConnectivityService.__init__` 加 `self.mappings = TopicMappingRepository(db)`。

在类末尾追加：

```python
    # ---- Topic Mappings ----

    def add_mapping(self, topic_id: int, action_type: str,
                    action_params: dict | None = None, field_path: str | None = None,
                    condition_expr: str | None = None, priority: int = 100,
                    description: str | None = None) -> TopicMapping:
        t = self.topics.get(topic_id)
        if t is None:
            raise NotFoundError(f"topic 不存在: {topic_id}")
        # Validate action_type
        valid = {"log_event", "update_work_order_produced_qty", "set_work_order_status",
                 "update_serial_unit_status", "create_defect", "webhook_forward"}
        if action_type not in valid:
            raise ValidationError(f"action_type 必须是 {sorted(valid)} 之一: {action_type}")
        # Parse action_params if string
        if isinstance(action_params, str):
            try:
                import json
                action_params = json.loads(action_params) if action_params.strip() else None
            except json.JSONDecodeError:
                raise ValidationError(f"action_params 不是有效 JSON: {action_params}")
        return self.mappings.add(TopicMapping(
            machine_topic_id=topic_id, action_type=action_type,
            action_params=action_params, field_path=field_path or None,
            condition_expr=condition_expr or None, priority=priority,
            description=description, is_active=True))

    def toggle_mapping(self, topic_id: int, mapping_id: int) -> TopicMapping:
        m = self.mappings.get(mapping_id)
        if m is None or m.machine_topic_id != topic_id:
            raise NotFoundError(f"mapping 不存在: {mapping_id}")
        m.is_active = not m.is_active
        self.db.flush()
        return m

    def delete_mapping(self, topic_id: int, mapping_id: int) -> None:
        m = self.mappings.get(mapping_id)
        if m is None or m.machine_topic_id != topic_id:
            raise NotFoundError(f"mapping 不存在: {mapping_id}")
        self.mappings.delete(mapping_id)

    def list_mappings(self, topic_id: int) -> list[TopicMapping]:
        return self.mappings.list_for_topic(topic_id)
```

- [ ] **Step 3: 加 Router 路由**

在 `src/lightmes/modules/connectivity/router.py` 末尾追加：

```python
@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/mappings",
             response_class=HTMLResponse)
def mapping_add(
    conn_id: int,
    topic_id: int,
    action_type: str = Form(...),
    action_params: str = Form(""),
    field_path: str = Form(""),
    condition_expr: str = Form(""),
    priority: int = Form(100),
    description: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.add_mapping(
            topic_id=topic_id, action_type=action_type,
            action_params=action_params or None,
            field_path=field_path or None,
            condition_expr=condition_expr or None,
            priority=priority,
            description=description or None)
        db.commit()
    except DomainError as e:
        return HTMLResponse(f"添加失败: {e.detail}", status_code=400)
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/mappings/{mid}/toggle",
             response_class=HTMLResponse)
def mapping_toggle(
    conn_id: int, topic_id: int, mid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.toggle_mapping(topic_id, mid)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/mappings/{mid}/delete",
             response_class=HTMLResponse)
def mapping_delete(
    conn_id: int, topic_id: int, mid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.delete_mapping(topic_id, mid)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)
```

- [ ] **Step 4: 增强 connection_detail.html 模板**

在 `connection_detail.html` 的 Topic 表格之后、消息表格之前，为每个 topic 展示其 mappings。修改模板，在 `{% for t in topics %}` 行的 `{% endfor %}` 之后追加一个 mappings 区域（所有 topic 的 mappings 集中展示）：

```html
<div class="card">
  <div class="card__title">Action Mappings（全部 topics）</div>
  {% for t in topics %}
    {% set mappings = svc_list_mappings(t.id) if false else [] %}
    {# Mappings are passed from router via context; add "all_mappings" dict in router #}
  {% endfor %}
  {# 简化：router 传 all_mappings as dict[topic_id -> list[TopicMapping]] #}
  {% for topic, maps in all_mappings.items() %}
    <div style="margin-bottom:12px">
      <strong>{{ topic.topic_pattern }}</strong>
      {% if maps %}
      <table class="data-table" style="margin-top:4px">
        <thead><tr><th>优先级</th><th>Action</th><th>字段</th><th>条件</th><th>参数</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          {% for m in maps %}
          <tr>
            <td>{{ m.priority }}</td>
            <td><code>{{ m.action_type }}</code></td>
            <td><code>{{ m.field_path or '—' }}</code></td>
            <td><code>{{ m.condition_expr or '总是' }}</code></td>
            <td><code style="font-size:11px;word-break:break-all">{{ m.action_params or '{}' }}</code></td>
            <td>{% if m.is_active %}✓{% else %}✗{% endif %}</td>
            <td>
              <form method="post" action="/connectivity/connections/{{ conn.id }}/topics/{{ topic.id }}/mappings/{{ m.id }}/toggle" style="display:inline">
                <button type="submit">{% if m.is_active %}停用{% else %}启用{% endif %}</button>
              </form>
              <form method="post" action="/connectivity/connections/{{ conn.id }}/topics/{{ topic.id }}/mappings/{{ m.id }}/delete" style="display:inline"
                    onsubmit="return confirm('确认删除？')">
                <button type="submit" class="btn-danger">删</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% endif %}
      <form method="post" action="/connectivity/connections/{{ conn.id }}/topics/{{ topic.id }}/mappings" class="form-row" style="margin-top:4px">
        <select name="action_type">
          <option value="log_event">log_event</option>
          <option value="update_work_order_produced_qty">update_wo_qty</option>
          <option value="set_work_order_status">set_wo_status</option>
          <option value="update_serial_unit_status">update_sn_status</option>
          <option value="create_defect">create_defect</option>
          <option value="webhook_forward">webhook_forward</option>
        </select>
        <input name="field_path" placeholder="$.count" style="max-width:120px">
        <input name="condition_expr" placeholder="value > 0" style="max-width:120px">
        <input name="action_params" placeholder='{"qty_increment": true}' style="flex:2;font-size:12px">
        <input name="priority" type="number" value="100" style="max-width:60px">
        <button type="submit">加</button>
      </form>
    </div>
  {% endfor %}
</div>
```

- [ ] **Step 5: 修改 connection_detail router 传入 all_mappings**

在 `connection_detail` 函数中，在返回模板之前，组装 mappings：

```python
    # 查所有 topic 的 mappings
    all_mappings = {}
    for t in topics:
        all_mappings[t] = svc.list_mappings(t.id)
```

然后加入 context：

```python
    return templates.TemplateResponse(request, "connectivity/connection_detail.html", {
        ...
        "all_mappings": all_mappings,
    })
```

- [ ] **Step 6: 写测试**

在 `tests/modules/connectivity/test_router.py` 末尾追加：

```python
def test_mapping_add_via_post(client, db_session):
    _login_admin(client, db_session, "m1")
    c = _make_conn(db_session, "mapping-add")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
            "action_type": "log_event", "priority": "50"})
    assert resp.status_code in (200, 303)
    from lightmes.modules.connectivity.models import TopicMapping
    m = db_session.query(TopicMapping).filter(
        TopicMapping.machine_topic_id == t.id).one()
    assert m.action_type == "log_event"


def test_mapping_delete_via_post(client, db_session):
    _login_admin(client, db_session, "m2")
    c = _make_conn(db_session, "mapping-del")
    client.post(f"/connectivity/connections/{c.id}/topics", data={
        "topic_pattern": "machine/x", "payload_format": "json"})
    from lightmes.modules.connectivity.models import MachineTopic
    t = db_session.query(MachineTopic).filter(
        MachineTopic.machine_connection_id == c.id).one()
    client.post(f"/connectivity/connections/{c.id}/topics/{t.id}/mappings", data={
        "action_type": "log_event"})
    from lightmes.modules.connectivity.models import TopicMapping
    m = db_session.query(TopicMapping).filter(
        TopicMapping.machine_topic_id == t.id).one()
    resp = client.post(
        f"/connectivity/connections/{c.id}/topics/{t.id}/mappings/{m.id}/delete")
    assert resp.status_code in (200, 303)
    assert db_session.get(TopicMapping, m.id) is None
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/connectivity/ -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/connectivity/service.py \
        src/lightmes/modules/connectivity/repository.py \
        src/lightmes/modules/connectivity/router.py \
        src/lightmes/templates/connectivity/connection_detail.html \
        tests/modules/connectivity/test_router.py
git commit -m "feat(connectivity): TopicMapping CRUD service + admin UI + mapping routes"
```

---

### Task 6: 回归 + memory

**Files:**
- Modify: memory file
- Test: full regression

- [ ] **Step 1: 全套 connectivity 测试**

Run: `uv run pytest tests/modules/connectivity/ -v`
Expected: all PASS (~60+ tests).

- [ ] **Step 2: Migration round-trip**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: clean.

- [ ] **Step 3: 更新 memory**

在 `project_lightmes.md` 的 connectivity section 末尾追加：

```markdown
- **数采-2**（2026-08-13 完成）：TopicMapping 表 + MqttMessageParser（JSON/plain/csv/hex + JSONPath + 条件表达式）+ ActionExecutor（6 actions: log_event/update_wo_qty/set_wo_status/update_sn_status/create_defect/webhook_forward）+ persist_message 增强（解析→条件→执行→入库）+ Admin UI Mapping CRUD
```

- [ ] **Step 4: Commit (if any tracked files changed)**

If only memory changed (outside repo), no commit needed.
