# P2h 双层全景 + 工序级跳站 + 返工站位选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工位作业富主界面的"全景"从单层路线扩展为双层（路线级 + 作业站级）；启用工序级跳站（supervisor 授权，operation_record.result="skip"）；返工发起时选定预期返工站位写入 `SerialUnit.rework_target_station_id`，首次 re-pass 时硬卡该站位，重新发起返工可覆盖站位。

**Architecture:** `SerialUnit` 加 `rework_target_station_id` 字段（FK work_stations.id, nullable）+ Alembic 迁移；`OperationPassService` 拆出 `skip_operation` 方法（复用定位/防跳站/乐观锁，跳过技能/BOM/绑定/完工）+ `pass_operation` 插入 5a（返工站位硬卡）/6a（清字段）两步；`StationService.load` 取 `latest_result_by_op`（每工序最新记录的 result）构建 `skipped` 状态 + `station_operations`（Layer 2 子集）；`ReworkService.rework` 加 `expected_repass_station_id` 参数 + allowed 校验 + 放宽 `target_seq == current_operation_seq`（reworking 态）；新路由 `GET /production/station/skip-form` + `POST /production/station/skip`（supervisor 守卫）+ `GET /trace/rework/allowed-stations`；模板加 Layer 2 全景条 + 跳站模态框 + rework 站位下拉。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0 风格（`Mapped[]`/`mapped_column`，继承 Base+TimestampMixin）；Alembic 迁移；autogenerate 后**打开迁移确认只动预期表/索引**，不得误删既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component/uq_operator_skill_user_skill/uq_operation_work_station）。
- **`SerialUnit.rework_target_station_id: Mapped[int | None]`** FK->work_stations.id, default None。不变式：rework 时设值，首次 re-pass 后清 null，非返工态恒 null（service 层保证，不加 DB CHECK）。
- **跳站授权**：`supervisor`/`admin` 角色强制；路由层守卫，service 层不重复校验。
- **`skip_operation`** 复用 `pass_operation` 的步骤 1+3（定位）/2（WO 状态）/4（期望工序）/5（3 层防跳站）/6（乐观锁）/10（状态复位）/11（事件）；**跳过** 5b（技能）/5c（BOM）/7（绑定）/8（参数）/9（完工）。末工序不可跳。
- **`pass_operation` 新步骤**：5a（防跳站后、技能前，返工站位硬卡）+ 6a（写记录后，清 rework_target_station_id）。
- **`StationOpView` 加 `operation_id: int`**（Layer 2 过滤用）；`status` 加 `"skipped"` 值；移除 `was_skipped`（status 已表达）。
- **`StationView.station_operations: list[StationOpView]`** = 本站 allowed 子集。
- **`latest_result_by_op`**：取 SN 全部 operation_records，按 operation_id 分组取 end_time 最新的 result。被 re-pass 修正后的 skip 显示为 done。
- **`ReworkService.rework`** 加 `expected_repass_station_id: int` 参数；校验 ∈ 首个 re-pass 工序（seq > target_seq 第一道）allowed 集合；放宽校验 `target_seq > current_operation_seq` 拒绝（原 `>=`），reworking 态允许 `==`（重选站位）。
- **`OperationSkipped` 事件**：与 `OperationPassed` 平行，字段含 `reason: str`。
- **operator_id 服务端赋值**（防伪造）；写操作 require_login；DomainError -> `db.rollback()` + 错误片段。NO CDN；Jinja2 `{{ }}` 自动转义。
- 提交前缀 `feat:`/`refactor:`/`test:`/`docs:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

P2h 结束时新增/修改：

```
src/lightmes/modules/production/
├── models.py                    # 改：SerialUnit 加 rework_target_station_id
├── schemas.py                   # 改：StationOpView 加 operation_id；StationView 加 station_operations；新增 OperationSkipInput/Result
├── operation_pass_service.py    # 改：新增 skip_operation；pass_operation 加 5a/6a
├── station_service.py           # 改：load 取 latest_result_by_op + 构建 station_operations
├── events.py                    # 改：新增 OperationSkipped
└── router.py                    # 改：新增 GET /production/station/skip-form + POST /production/station/skip；station 路由注入 can_skip
src/lightmes/modules/trace/
├── rework_service.py            # 改：rework 加 expected_repass_station_id + allowed 校验 + 放宽 target_seq
├── schemas.py                   # 改：ReworkInput 加 expected_repass_station_id
└── router.py                    # 改：新增 GET /trace/rework/allowed-stations；rework POST 接收新字段
src/lightmes/migrations/versions/  # 新：add_rework_target_station_to_serial_units
src/lightmes/templates/production/
├── station_view.html            # 改：Layer 2 全景条 + skipped 状态 + 跳站按钮启用 + 模态框
└── partials/station_skip_form.html  # 新：跳站表单片段
src/lightmes/templates/trace/
├── rework.html                  # 改：target_seq onblur HTMX + 站位下拉容器
└── partials/rework_allowed_stations.html  # 新：站位 select 片段
src/lightmes/templates/trace/partials/rework_success.html  # 改：显示选中站名
src/lightmes/static/css/app.css  # 改：.station__step--skipped + .station__path--station + .modal
tests/modules/production/  # skip_operation + pass_operation 硬卡 + station_service 双层
tests/modules/trace/      # rework station selection
tests/modules/production/test_station_e2e.py  # 改：跳站 + 返工站位 E2E
```

---

### Task 1: SerialUnit.rework_target_station_id 字段 + 迁移

**Files:**
- Modify: `src/lightmes/modules/production/models.py`（SerialUnit 加字段）
- Create: `src/lightmes/migrations/versions/<auto>_add_rework_target_station_to_serial_units.py`
- Test: `tests/modules/production/test_models_serial_unit.py`（改：加字段断言）

**Interfaces:**
- Produces:
  - `SerialUnit.rework_target_station_id: Mapped[int | None]`（FK work_stations.id, default None）
  - 迁移：`ALTER TABLE serial_units ADD COLUMN rework_target_station_id INTEGER REFERENCES work_stations(id)`

- [ ] **Step 1: 加字段到 SerialUnit 模型**

在 `src/lightmes/modules/production/models.py` 的 `SerialUnit` 类（第 41-59 行），在 `carrier_code` 字段之后加：
```python
    carrier_code: Mapped[str | None] = mapped_column(default=None)
    # 返工时设定的预期 re-pass 站位；首次 re-pass 后清 null（service 层保证）
    rework_target_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), default=None
    )
```

- [ ] **Step 2: 生成迁移**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "add_rework_target_station_to_serial_units"
```
Expected: 生成 `src/lightmes/migrations/versions/<hash>_add_rework_target_station_to_serial_units.py`

- [ ] **Step 3: 校验迁移只动 serial_units**

打开生成的迁移文件，确认 `upgrade()` 仅含：
```python
def upgrade():
    op.add_column(
        "serial_units",
        sa.Column("rework_target_station_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_serial_units_rework_target_station_id_work_stations",
        "serial_units",
        "work_stations",
        ["rework_target_station_id"],
        ["id"],
    )

def downgrade():
    op.drop_constraint(
        "fk_serial_units_rework_target_station_id_work_stations",
        "serial_units",
        type_="foreignkey",
    )
    op.drop_column("serial_units", "rework_target_station_id")
```
若 autogenerate 误删其他索引/约束，**手动删掉那些 op 行**，只保留上述 add_column + create_foreign_key。

- [ ] **Step 4: 跑迁移**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 输出 `Running upgrade <prev> -> <new>, add_rework_target_station_to_serial_units`

- [ ] **Step 5: 加字段断言测试**

在 `tests/modules/production/test_models_serial_unit.py` 末尾加（若文件无 `_make_su` 辅助则参考既有测试构造一个 SerialUnit）：
```python
def test_serial_unit_rework_target_station_id_default_none(db_session):
    from lightmes.modules.production.models import SerialUnit
    from lightmes.modules.production.repository import SerialUnitRepository, WorkOrderRepository
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate

    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="RWT", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="RWS", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="RWP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1",
                           default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="RWRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code="RWSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1,
        sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    su = SerialUnitRepository(db_session).first_pending_by_work_order(wo.id)
    assert su.rework_target_station_id is None
```

- [ ] **Step 6: 跑测试**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_models_serial_unit.py -v
```
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/modules/production/models.py src/lightmes/migrations/versions/ src/lightmes/migrations/versions/*_add_rework_target_station_to_serial_units.py tests/modules/production/test_models_serial_unit.py
git commit -m "feat: add SerialUnit.rework_target_station_id field + migration"
```

---

### Task 2: Schemas + OperationSkipped 事件

**Files:**
- Modify: `src/lightmes/modules/production/schemas.py`（StationOpView/StationView/OperationSkipInput/Result）
- Modify: `src/lightmes/modules/production/events.py`（OperationSkipped）
- Modify: `src/lightmes/modules/trace/schemas.py`（ReworkInput 加字段）
- Test: `tests/modules/production/test_events.py`（改：加 OperationSkipped 断言）

**Interfaces:**
- Produces:
  - `StationOpView.operation_id: int`
  - `StationView.station_operations: list[StationOpView]`
  - `OperationSkipInput(work_station_id, sn, work_order_code, operator_id, reason)`
  - `OperationSkipResult(sn, skipped_op, next_op, is_finished, work_order_status, next_op_can_continue_here)`
  - `OperationSkipped` 事件（serial_unit_id, sn, work_order_id, operation_id, work_station_id, line_id, reason）
  - `ReworkInput.expected_repass_station_id: int`

- [ ] **Step 1: 改 StationOpView + StationView**

在 `src/lightmes/modules/production/schemas.py` 找到 `StationOpView`（第 94-100 行），改为：
```python
class StationOpView(BaseModel):
    operation_id: int  # 新增：Layer 2 过滤用
    seq: int
    name: str
    code: str
    work_station_id: int
    status: str  # "done" | "current" | "future" | "skipped"
    allowed_work_stations: list[str] = []
```

找到 `StationView`（第 128-145 行），在 `operations` 字段后加：
```python
    operations: list[StationOpView]
    station_operations: list[StationOpView] = []  # 新增：Layer 2（本站 allowed 子集）
    current_op: StationOpView | None
```

- [ ] **Step 2: 加 OperationSkipInput/Result**

在 `src/lightmes/modules/production/schemas.py` 的 `OperationPassResult` 类之后（第 85 行后）加：
```python
class OperationSkipInput(BaseModel):
    work_station_id: int
    sn: str | None = None
    work_order_code: str | None = None
    operator_id: int | None = None
    reason: str  # 必填


class OperationSkipResult(BaseModel):
    sn: str
    skipped_op: OpInfo
    next_op: OpInfo | None
    is_finished: bool  # 恒 False（末工序不可跳）
    work_order_status: str
    next_op_can_continue_here: bool = False
```

- [ ] **Step 3: 加 OperationSkipped 事件**

在 `src/lightmes/modules/production/events.py` 末尾加：
```python
@dataclass
class OperationSkipped(Event):
    serial_unit_id: int
    sn: str
    work_order_id: int
    operation_id: int
    work_station_id: int
    line_id: int
    reason: str
```

- [ ] **Step 4: 改 ReworkInput**

在 `src/lightmes/modules/trace/schemas.py` 末尾加（若已有 `ReworkInput` 则改其字段；当前文件无此类，但 router 直接用 Form 参数--本步改为在 schemas.py 定义以便 service 层用）：
```python
class ReworkInput(BaseModel):
    sn: str
    target_seq: int
    expected_repass_station_id: int  # 新增：必填
    unbind_bind_ids: list[int] | None = None
    reason: str | None = None
```

- [ ] **Step 5: 加事件测试**

在 `tests/modules/production/test_events.py` 末尾加（参考既有 `OperationPassed` 测试模式）：
```python
def test_operation_skipped_event_published():
    from lightmes.modules.production.events import OperationSkipped
    from lightmes.shared.events import event_bus
    received = []
    event_bus.subscribe(OperationSkipped, lambda e: received.append(e))
    ev = OperationSkipped(
        serial_unit_id=1, sn="SN001", work_order_id=2, operation_id=3,
        work_station_id=4, line_id=5, reason="测试跳过")
    event_bus.publish(ev)
    assert received == [ev]
    assert received[0].reason == "测试跳过"
```

- [ ] **Step 6: 跑测试**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_events.py -v
```
Expected: PASS（含新测试）

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/modules/production/schemas.py src/lightmes/modules/production/events.py src/lightmes/modules/trace/schemas.py tests/modules/production/test_events.py
git commit -m "feat: add skip schemas + OperationSkipped event + ReworkInput.expected_repass_station_id"
```

---

### Task 3: OperationPassService.skip_operation

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（新增 skip_operation 方法）
- Test: `tests/modules/production/test_operation_pass_skip.py`（新）

**Interfaces:**
- Consumes: `OperationSkipInput`（Task 2）、`OperationSkipped` 事件（Task 2）、`MasterDataQueryService`、`SerialUnitRepository`、`OperationRecordRepository`
- Produces:
  - `OperationPassService.skip_operation(data: OperationSkipInput) -> OperationSkipResult`
  - 行为：复用定位/防跳站/乐观锁；跳过技能/BOM/绑定/参数/完工；末工序拒绝；写 `OperationRecord(result="skip", remark=reason)`；发 `OperationSkipped`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_operation_pass_skip.py`：
```python
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import OperationRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, n_ops=3):
    md = MasterDataService(db_session)
    user = User(username="skipop", password_hash="x", display_name="主管")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="SKL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SKW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SKP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])
        for i in range(n_ops)
    ]
    routing = md.create_routing(RoutingCreate(code="SKRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code="SKSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SKWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1,
        sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, ws, user, wo


def test_skip_advances_seq_and_writes_skip_record(db_session):
    db, ws, user, wo = _setup(db_session)
    # 先 pass 第一道
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 跳过第二道
    result = OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="临时取消"))
    assert result.skipped_op.seq == 2
    assert result.next_op.seq == 3
    assert result.is_finished is False
    # 验证 skip 记录
    rec = db.execute(select(OperationRecord).where(
        OperationRecord.serial_unit_id == su.id,
        OperationRecord.result == "skip")).scalar_one()
    assert rec.remark == "临时取消"
    # 验证 seq 推进
    db.refresh(su)
    assert su.current_operation_seq == 2


def test_skip_last_op_rejected(db_session):
    db, ws, user, wo = _setup(db_session, n_ops=2)
    # pass 第一道
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 跳过末道（第二道）-> 拒绝
    with pytest.raises(BusinessRuleError, match="末工序不可跳过"):
        OperationPassService(db).skip_operation(OperationSkipInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="试图跳末道"))


def test_skip_publishes_operation_skipped_event(db_session):
    from lightmes.modules.production.events import OperationSkipped
    from lightmes.shared.events import event_bus
    received = []
    event_bus.subscribe(OperationSkipped, lambda e: received.append(e))
    db, ws, user, wo = _setup(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="事件测试"))
    assert len(received) == 1
    assert received[0].reason == "事件测试"
    assert received[0].operation_id is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_skip.py -v
```
Expected: FAIL with `AttributeError: 'OperationPassService' object has no attribute 'skip_operation'`

- [ ] **Step 3: 实现 skip_operation**

在 `src/lightmes/modules/production/operation_pass_service.py` 的 `OperationPassService` 类末尾（`pass_operation` 方法之后）加：
```python
    def skip_operation(self, data: OperationSkipInput) -> OperationSkipResult:
        """跳过当前工序：写 result='skip' 记录，推进 seq，不绑料/不校验技能/不完工。"""
        # 1+3. 定位单元：SN -> 载体码 -> 工单号(取第一个 pending)
        su = None
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                su = self.serial_units.get_active_by_carrier(data.sn)
            if su is None:
                raise NotFoundError(f"未找到 SN 或载体码: {data.sn}")
            if su.status in ("finished", "scrapped"):
                raise BusinessRuleError(f"SN 已{su.status}，不可跳站: {su.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首件跳站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = self.serial_units.first_pending_by_work_order(wo.id)
            if su is None:
                raise BusinessRuleError("工单 SN 已全部投产")

        # 2. 工单状态
        if wo.status not in ("released", "in_process"):
            raise BusinessRuleError(f"工单状态不允许跳站: {wo.status}")

        operations = self.query.get_operations(wo.routing_id)
        if not operations:
            raise BusinessRuleError("工艺路径无工序")

        # 4. 期望下一工序
        next_ops = [o for o in operations if o.seq > su.current_operation_seq]
        if not next_ops:
            raise BusinessRuleError("已完工，无后续工序")
        expected = next_ops[0]

        # 末工序不可跳
        if expected.id == operations[-1].id:
            raise BusinessRuleError("末工序不可跳过")

        # 5. 三层防跳站
        ws = self.query.get_work_station(data.work_station_id)
        if ws is None:
            raise NotFoundError(f"作业站不存在: {data.work_station_id}")
        if ws.line_id != wo.line_id:
            raise BusinessRuleError("当前作业站不属于本工单产线")
        allowed = self.query.get_allowed_work_stations(expected.id)
        allowed_ids = [w.id for w in allowed] or [expected.default_work_station_id]
        if data.work_station_id not in allowed_ids:
            names = "、".join(w.name for w in allowed) or f"作业站 #{expected.default_work_station_id}"
            raise BusinessRuleError(
                f"该 SN 当前工序 {expected.seq} {expected.name} "
                f"应在【{names}】之一作业站做，当前作业站不符")

        # 6. 写工序记录 + 乐观锁推进 seq（跳过技能/BOM/绑定/参数/完工）
        record = self.records.add(OperationRecord(
            serial_unit_id=su.id, work_order_id=wo.id, operation_id=expected.id,
            work_station_id=data.work_station_id, line_id=wo.line_id,
            operator_id=data.operator_id, result="skip", remark=data.reason,
        ))
        prev_version = su.version
        r = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(current_operation_seq=expected.seq, version=prev_version + 1)
        )
        if r.rowcount == 0:
            raise ConflictError("该产品正被其他作业站处理，请重试")
        self.db.refresh(su)

        # 10. 工单/返工件状态复位（skip 不完工）
        if wo.status == "released":
            wo.status = "in_process"
        if su.status in ("reworking", "pending"):
            su.status = "in_process"
        self.db.flush()

        # 11. 事件
        event_bus.publish(OperationSkipped(
            serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            operation_id=expected.id, work_station_id=data.work_station_id,
            line_id=wo.line_id, reason=data.reason))

        remaining = [o for o in operations if o.seq > expected.seq]
        next_info = None
        next_op_can_continue_here = False
        if remaining:
            next_op_obj = remaining[0]
            next_info = OpInfo(seq=next_op_obj.seq, name=next_op_obj.name,
                               work_station_id=next_op_obj.default_work_station_id)
            next_allowed = self.query.get_allowed_work_stations(next_op_obj.id)
            next_allowed_ids = [w.id for w in next_allowed] or [next_op_obj.default_work_station_id]
            next_op_can_continue_here = data.work_station_id in next_allowed_ids
        return OperationSkipResult(
            sn=su.sn,
            skipped_op=OpInfo(seq=expected.seq, name=expected.name,
                              work_station_id=expected.default_work_station_id),
            next_op=next_info, is_finished=False,
            work_order_status=wo.status,
            next_op_can_continue_here=next_op_can_continue_here,
        )
```

文件顶部 import 加：
```python
from lightmes.modules.production.schemas import (
    OperationPassInput, OperationPassResult, OperationSkipInput, OperationSkipResult, OpInfo,
)
from lightmes.modules.production.events import OperationPassed, OperationSkipped, SerialUnitFinished
```
（在既有 import 基础上追加 `OperationSkipInput, OperationSkipResult, OperationSkipped`。）

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_skip.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 跑回归确认 pass 不受影响**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skill.py tests/modules/production/test_operation_work_station_pass.py -v
```
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/operation_pass_service.py tests/modules/production/test_operation_pass_skip.py
git commit -m "feat: add OperationPassService.skip_operation (supervisor-authorized, no BOM/skill/finish)"
```

---

### Task 4: pass_operation 加返工站位硬卡（5a + 6a）

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（pass_operation 插入 5a/6a）
- Test: `tests/modules/production/test_operation_pass_rework_station.py`（新）

**Interfaces:**
- Consumes: `SerialUnit.rework_target_station_id`（Task 1）、`MasterDataQueryService.get_work_station`
- Produces:
  - `pass_operation` 在步骤 5 后、5b 前插入 5a（reworking + 字段非 null 时校验 work_station_id == rework_target_station_id）
  - `pass_operation` 在步骤 6 后插入 6a（首次 re-pass 成功后清 rework_target_station_id = None）

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_operation_pass_rework_station.py`：
```python
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, n_ops=3):
    md = MasterDataService(db_session)
    user = User(username="rwop", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="RWL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="RWP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id])
        for i in range(n_ops)
    ]
    routing = md.create_routing(RoutingCreate(code="RWRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(
        code="RWSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(
        code="RWWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1,
        sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, (ws1, ws2), user, wo


def test_rework_first_repass_wrong_station_blocked(db_session):
    db, (ws1, ws2), user, wo = _setup(db_session)
    # pass op1 @ ws1, op2 @ ws1
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    # 返工到 op1，预期 re-pass @ ws2
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    db.refresh(su)
    assert su.status == "reworking"
    assert su.rework_target_station_id == ws2.id
    # 试图在 ws1 re-pass -> 拒绝
    with pytest.raises(BusinessRuleError, match="该返工件须在【站2】重做"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    # 字段保留（未消费）
    db.refresh(su)
    assert su.rework_target_station_id == ws2.id


def test_rework_first_repass_correct_station_clears_field(db_session):
    db, (ws1, ws2), user, wo = _setup(db_session)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    # 在 ws2 re-pass -> 通过，字段清空
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.rework_target_station_id is None
    assert su.status == "in_process"


def test_subsequent_repass_not_blocked(db_session):
    db, (ws1, ws2), user, wo = _setup(db_session, n_ops=4)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    ReworkService(db).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    # 首次 re-pass @ ws2
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws2.id, sn=su.sn, operator_id=user.id))
    # 二次 re-pass（下一工序）@ ws1 -> 不卡
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, sn=su.sn, operator_id=user.id))
    db.refresh(su)
    assert su.current_operation_seq == 2
```

**注**：`ReworkService.rework` 在 Task 6 才加 `expected_repass_station_id` 参数。本 Task 测试会因 `rework()` 缺参数失败。**执行顺序**：先写本测试（红），Task 6 实现 `rework` 后本测试转绿。或在 Task 6 之后再跑本测试。**推荐**：本 Task 先实现 pass_operation 的 5a/6a，测试用直接设 `su.rework_target_station_id` 绕过 rework_service：

替换 `test_rework_first_repass_wrong_station_blocked` 中 `ReworkService(db).rework(...)` 调用为：
```python
    su.status = "reworking"
    su.current_operation_seq = 0  # target_seq
    su.rework_target_station_id = ws2.id
    db_session.flush()
```
（直接设字段，不依赖 ReworkService。Task 6 实现后再补集成测试。）

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_rework_station.py -v
```
Expected: FAIL（pass_operation 未卡站位，wrong-station 测试会过站成功而非抛错）

- [ ] **Step 3: 实现 5a + 6a**

在 `src/lightmes/modules/production/operation_pass_service.py` 的 `pass_operation` 方法中，找到步骤 5（防跳站，第 72-87 行 `# 5. 三层防跳站`）结束、步骤 5b（`# 5b. 技能校验`，第 89 行）之前，插入：
```python
        # 5a. 返工首次 re-pass 站位硬卡（仅 reworking 态 + 已设预期站位时生效）
        if su.status == "reworking" and su.rework_target_station_id is not None:
            if data.work_station_id != su.rework_target_station_id:
                expected_ws = self.query.get_work_station(su.rework_target_station_id)
                current_ws = ws  # 步骤 5 已查
                raise BusinessRuleError(
                    f"该返工件须在【{expected_ws.name if expected_ws else f'#{su.rework_target_station_id}'}】重做，"
                    f"当前作业站【{current_ws.name if current_ws else f'#{data.work_station_id}'}】不符。"
                    f"如需更改，请重新发起返工选择正确站位。")
```

找到步骤 6（写记录 + 乐观锁，第 132-146 行）结束、步骤 7（`# 7. 绑料`，第 148 行）之前，插入：
```python
        # 6a. 首次 re-pass 成功后清除返工站位约束
        if su.status == "reworking" and su.rework_target_station_id is not None:
            su.rework_target_station_id = None
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_rework_station.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: 跑回归**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skill.py tests/modules/production/test_operation_work_station_pass.py tests/modules/production/test_pass_carrier.py -v
```
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/operation_pass_service.py tests/modules/production/test_operation_pass_rework_station.py
git commit -m "feat: pass_operation hard-blocks rework first re-pass at expected station (5a/6a)"
```

---

### Task 5: StationService.load 双层全景 + skipped 状态

**Files:**
- Modify: `src/lightmes/modules/production/station_service.py`（load 取 latest_result_by_op + 构建 station_operations）
- Test: `tests/modules/production/test_station_service.py`（改：加双层 + skipped 断言）

**Interfaces:**
- Consumes: `StationOpView.operation_id`（Task 2）、`StationView.station_operations`（Task 2）、`OperationRecord`
- Produces:
  - `StationService.load` 返回的 `StationView` 含 `station_operations`（Layer 2 子集）+ 各 op 的 `status` 可为 `"skipped"`

- [ ] **Step 1: 写失败测试**

在 `tests/modules/production/test_station_service.py` 末尾加（参考既有 `_setup` 辅助；若无则参考 Task 3 的 `_setup`）：
```python
def test_load_station_operations_subset(db_session):
    """Layer 2 仅含本站 allowed 工序子集。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.station_service import StationService
    from lightmes.modules.auth.models import User

    md = MasterDataService(db_session)
    user = User(username="ssop", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="SSL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="SS1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="SS2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="SSP", name="件", type="finished"))
    # op1 仅 ws1，op2 仅 ws2，op3 ws1+ws2
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
        OperationCreate(seq=3, code="OP3", name="工序3", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SSRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SSSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="SSWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 在 ws1 加载（首件 pending）
    view = StationService(db_session).load("SSWO", ws1.id, user.id)
    # Layer 2 应仅含 op1, op3（ws1 allowed）
    station_op_seqs = [o.seq for o in view.station_operations]
    assert station_op_seqs == [1, 3]


def test_load_skipped_status_after_skip(db_session):
    """跳站后 Layer 1 显示 skipped 状态。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput, OperationSkipInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.station_service import StationService
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.auth.models import User

    md = MasterDataService(db_session)
    user = User(username="skip2", password_hash="x", display_name="主管")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="SKL2", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SKW2", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SKP2", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=3, code="OP3", name="工序3", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SKRT2", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SKSR2", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="SKWO2", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # pass op1, skip op2
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SKWO2", operator_id=user.id))
    su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]
    OperationPassService(db_session).skip_operation(OperationSkipInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id, reason="跳过 op2"))
    # 加载
    view = StationService(db_session).load(su.sn, ws.id, user.id)
    status_by_seq = {o.seq: o.status for o in view.operations}
    assert status_by_seq[1] == "done"
    assert status_by_seq[2] == "skipped"
    assert status_by_seq[3] == "current"
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_service.py::test_load_station_operations_subset tests/modules/production/test_station_service.py::test_load_skipped_status_after_skip -v
```
Expected: FAIL（`station_operations` 字段不存在 / `skipped` 状态未渲染）

- [ ] **Step 3: 改 StationService.load**

在 `src/lightmes/modules/production/station_service.py` 顶部 import 加 `OperationRecord`：
```python
from lightmes.modules.production.models import (
    FirstInspectionConfig, TestDataTemplate, OperationRecord,
)
```
（在既有 import 基础上追加 `OperationRecord`。）

在 `load` 方法中，找到 `op_views: list[StationOpView] = []`（第 59 行）之前，加 `latest_result_by_op` 查询：
```python
        # 取该 SN 全部 operation_records，按 operation_id 分组取 end_time 最新的 result
        latest_result_by_op: dict[int, str] = {}
        if su is not None:
            all_records = list(self.db.execute(
                select(OperationRecord)
                .where(OperationRecord.serial_unit_id == su.id)
                .order_by(OperationRecord.operation_id, OperationRecord.end_time.desc())
            ).scalars().all())
            for r in all_records:
                if r.operation_id not in latest_result_by_op:
                    latest_result_by_op[r.operation_id] = r.result  # 第一条 = 最新
```

在 `op_views.append(StationOpView(...))` 处（第 72-76 行），改 status 判定 + 加 operation_id：
```python
        op_views: list[StationOpView] = []
        for o in operations:
            if o.seq > current_seq:
                st = "future"
            elif expected is not None and o.id == expected.id and su is not None and su.status != "finished":
                st = "current"
            elif latest_result_by_op.get(o.id) == "skip":
                st = "skipped"
            else:
                st = "done"
            op_allowed = op_ws_map.get(o.id, [])
            allowed_names = [w.name for w in op_allowed]
            if not allowed_names:
                ws = self.query.get_work_station(o.default_work_station_id)
                allowed_names = [ws.name if ws else f"#{o.default_work_station_id}"]
            op_views.append(StationOpView(
                operation_id=o.id,
                seq=o.seq, name=o.name, code=o.code,
                work_station_id=o.default_work_station_id, status=st,
                allowed_work_stations=allowed_names,
            ))
```

在 `return StationView(...)` 之前，构建 `station_op_views`：
```python
        # Layer 2：本作业站 allowed 子集
        station_op_views = [
            v for v in op_views
            if work_station_id in [w.id for w in op_ws_map.get(v.operation_id, [])]
        ]
```

在 `return StationView(...)` 调用中加 `station_operations=station_op_views`：
```python
        return StationView(
            # ... 既有字段 ...
            operations=op_views,
            station_operations=station_op_views,
            current_op=current_op,
            # ... 既有字段 ...
        )
```

文件顶部 import 加 `select`：
```python
from sqlalchemy import select
from sqlalchemy.orm import Session
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_service.py -v
```
Expected: 全绿（含新测试）

- [ ] **Step 5: 跑回归**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_e2e.py tests/modules/production/test_station_main_flow.py -v
```
Expected: 全绿（若因 `operation_id` 新字段导致既有断言失败，补适配）

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/station_service.py tests/modules/production/test_station_service.py
git commit -m "feat: StationService.load builds dual-layer panorama (route + station) + skipped status"
```

---

### Task 6: ReworkService.rework 站位选择 + 校验

**Files:**
- Modify: `src/lightmes/modules/trace/rework_service.py`（rework 加 expected_repass_station_id + allowed 校验 + 放宽 target_seq）
- Test: `tests/modules/trace/test_rework_service.py`（改：加站位校验 + 放宽断言）

**Interfaces:**
- Consumes: `SerialUnit.rework_target_station_id`（Task 1）、`MasterDataQueryService.get_operations` / `get_allowed_work_stations`
- Produces:
  - `ReworkService.rework(sn, target_seq, expected_repass_station_id, unbind_bind_ids, reason, operator_id) -> SerialUnit`
  - 行为：校验 expected_repass_station_id ∈ 首个 re-pass 工序 allowed；放宽 `target_seq > current_operation_seq` 拒绝（reworking 态允许 `==`）；写字段

- [ ] **Step 1: 写失败测试**

在 `tests/modules/trace/test_rework_service.py` 末尾加（参考既有 `_setup` 辅助）：
```python
def test_rework_writes_expected_repass_station(db_session):
    """rework 写入 rework_target_station_id。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.trace.rework_service import ReworkService
    from lightmes.modules.auth.models import User

    md = MasterDataService(db_session)
    user = User(username="rwop2", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="RWL2", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RW1b", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RW2b", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="RWP2", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RWRT2", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RWSR2", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="RWWO2", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO2", operator_id=user.id))
    su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]
    # 返工到 op1 之前（target_seq=0），预期 re-pass op1 @ ws2
    ReworkService(db_session).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    db_session.refresh(su)
    assert su.status == "reworking"
    assert su.current_operation_seq == 0
    assert su.rework_target_station_id == ws2.id


def test_rework_rejects_station_not_in_allowed(db_session):
    """expected_repass_station_id 不在 allowed 集合 -> 拒绝。"""
    # 复用 test_rework_writes_expected_repass_station 的 _setup，但 expected 站不在 allowed
    # 需要第三站 ws3 不在 op1 的 allowed
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.trace.rework_service import ReworkService
    from lightmes.modules.auth.models import User
    from lightmes.shared.errors import ValidationError

    md = MasterDataService(db_session)
    user = User(username="rwop3", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="RWL3", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RW1c", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RW2c", name="站2", line_id=line.id, seq=2))
    ws3 = md.create_work_station(WorkStationCreate(code="RW3c", name="站3", line_id=line.id, seq=3))
    p = md.create_product(ProductCreate(code="RWP3", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),  # ws3 不在
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RWRT3", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RWSR3", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="RWWO3", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO3", operator_id=user.id))
    su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]
    with pytest.raises(ValidationError, match="不在工序.*的允许集合内"):
        ReworkService(db_session).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws3.id)


def test_rework_reworking_allows_equal_target_seq(db_session):
    """reworking 态允许 target_seq == current_operation_seq（重选站位）。"""
    # 先返工到 op1 之前 + ws2，再重新发起返工到同样 seq + ws1
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.trace.rework_service import ReworkService
    from lightmes.modules.auth.models import User

    md = MasterDataService(db_session)
    user = User(username="rwop4", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="RWL4", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RW1d", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RW2d", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="RWP4", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RWRT4", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RWSR4", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="RWWO4", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RWWO4", operator_id=user.id))
    su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]
    # 第一次返工 target_seq=0, ws2
    ReworkService(db_session).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws2.id)
    # 重新发起 target_seq=0（== current）, ws1 -> 允许，覆盖字段
    ReworkService(db_session).rework(sn=su.sn, target_seq=0, expected_repass_station_id=ws1.id)
    db_session.refresh(su)
    assert su.rework_target_station_id == ws1.id
```
（文件顶部 `import pytest` 已有则不重复。）

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_rework_service.py -v
```
Expected: FAIL（`rework()` 缺 `expected_repass_station_id` 参数 -> TypeError）

- [ ] **Step 3: 改 ReworkService.rework**

在 `src/lightmes/modules/trace/rework_service.py` 顶部 import 加：
```python
from lightmes.modules.masterdata.query_service import MasterDataQueryService
```
`__init__` 加：
```python
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.genealogy = GenealogyService(db)
        self.carrier_bindings = CarrierBindingRepository(db)
        self.query = MasterDataQueryService(db)  # 新增
```

改 `rework` 方法签名 + 加校验 + 写字段：
```python
    def rework(
        self, sn: str, target_seq: int,
        expected_repass_station_id: int,
        unbind_bind_ids: list[int] | None = None,
        reason: str | None = None, operator_id: int | None = None,
    ) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "scrapped":
            raise BusinessRuleError(f"SN 已判废，不可返工: {sn}")
        # 放宽：原 `>=` 改 `>`；reworking 态允许 ==（重选站位）
        if target_seq < 0 or target_seq > su.current_operation_seq:
            raise ValidationError(
                f"返工目标工序 {target_seq} 必须小于等于当前 {su.current_operation_seq}")
        # 校验 expected 站 ∈ 首个 re-pass 工序 allowed
        wo = self.db.get(WorkOrder, su.work_order_id)
        operations = self.query.get_operations(wo.routing_id)
        first_repass_op = next((o for o in operations if o.seq > target_seq), None)
        if first_repass_op is None:
            raise ValidationError(f"target_seq {target_seq} 之后无工序可重做")
        allowed = self.query.get_allowed_work_stations(first_repass_op.id)
        allowed_ids = [w.id for w in allowed] or [first_repass_op.default_work_station_id]
        if expected_repass_station_id not in allowed_ids:
            raise ValidationError(
                f"站位 #{expected_repass_station_id} 不在工序 "
                f"{first_repass_op.seq} {first_repass_op.name} 的允许集合内")
        # 解绑组件
        for bind_id in (unbind_bind_ids or []):
            bind = self.genealogy.binds.get(bind_id)
            if bind is None or bind.parent_sn_id != su.id:
                raise NotFoundError(f"谱系绑定不存在或不属于本 SN: {bind_id}")
            self.genealogy.unbind(bind_id, reason=reason, operator_id=operator_id)
        prev_version = su.version
        result = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(status="reworking", current_operation_seq=target_seq,
                    rework_target_station_id=expected_repass_station_id,
                    version=prev_version + 1)
        )
        if result.rowcount == 0:
            raise ConflictError("该产品正被其他操作处理，请重试")
        self.db.refresh(su)
        event_bus.publish(SerialUnitReworkStarted(
            serial_unit_id=su.id, sn=su.sn, target_seq=target_seq,
        ))
        return su
```
顶部 import 加 `WorkOrder`：
```python
from lightmes.modules.production.models import SerialUnit, WorkOrder
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_rework_service.py -v
```
Expected: 全绿（含新测试 + 既有测试可能需适配新必填参数）

- [ ] **Step 5: 适配既有 rework 测试**

如果既有 `test_rework_service.py` 测试因 `rework()` 新增必填参数失败，在所有既有 `rework(...)` 调用处补 `expected_repass_station_id=ws.id`（用任意 allowed 站）。Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/ -v
```
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/trace/rework_service.py tests/modules/trace/test_rework_service.py
git commit -m "feat: ReworkService.rework accepts expected_repass_station_id + validates allowed + relaxes target_seq"
```

---

### Task 7: Skip 路由（GET form + POST，supervisor 守卫）

**Files:**
- Modify: `src/lightmes/modules/production/router.py`（新增 GET /production/station/skip-form + POST /production/station/skip；station 路由注入 can_skip）
- Test: `tests/modules/production/test_skip_routes.py`（新）

**Interfaces:**
- Consumes: `OperationPassService.skip_operation`（Task 3）、`StationService.load`（Task 5）、`current_user_or_none`
- Produces:
  - `GET /production/station/skip-form`（supervisor/admin 守卫，返回 `station_skip_form.html` 片段）
  - `POST /production/station/skip`（supervisor/admin 守卫，执行跳站，渲染 `station_pass_result.html`）
  - station 路由向 `station_view.html` 注入 `can_skip: bool`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_skip_routes.py`：
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import SessionLocal, engine
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository


def _login(client, username="skipadmin", password="pass123"):
    # 创建 supervisor 用户
    db = SessionLocal()
    try:
        auth = AuthService(db)
        role = Role(name="supervisor", display_name="主管", is_system=True)
        db.add(role); db.flush()
        user = User(username=username, password_hash=auth.hash_password(password),
                    display_name="主管", role_id=role.id)
        db.add(user); db.commit()
        user_id = user.id
    finally:
        db.close()
    # 登录
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code in (200, 303)
    return user_id


def test_skip_form_requires_supervisor(db_session):
    client = TestClient(app)
    # 不登录 -> 401
    resp = client.get("/production/station/skip-form", params={"work_station_id": 1, "scan": "X"})
    assert resp.status_code == 401


def test_skip_form_returns_form_for_supervisor(db_session):
    # 完整 setup + 登录 + 跳站 form
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="SKL3", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SKW3", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SKP3", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="SKRT3", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SKSR3", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="SKWO3", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # pass op1
    user_id = _login(TestClient(app))
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="SKWO3", operator_id=user.id))
        su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
        sn = su.sn
    finally:
        db.close()
    # 取 form
    client = TestClient(app)
    _login(client)
    resp = client.get("/production/station/skip-form", params={"work_station_id": ws.id, "scan": sn})
    assert resp.status_code == 200
    assert "确认跳过工序" in resp.text
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_skip_routes.py -v
```
Expected: FAIL（404 路由不存在）

- [ ] **Step 3: 实现路由**

在 `src/lightmes/modules/production/router.py` 中找到既有 `station_pass` 路由（POST /production/station/pass），在其后加：
```python
@router.get("/production/station/skip-form", response_class=HTMLResponse)
def station_skip_form(
    request: Request,
    work_station_id: int,
    scan: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if user.role not in ("admin", "supervisor"):
        return templates.TemplateResponse(
            request, "production/partials/station_enter_error.html",
            {"error": "仅主管/管理员可跳站"})
    try:
        view = StationService(db).load(scan, work_station_id, user.id)
    except DomainError as e:
        return templates.TemplateResponse(
            request, "production/partials/station_enter_error.html",
            {"error": str(e.detail)})
    return templates.TemplateResponse(
        request, "production/partials/station_skip_form.html",
        {"view": view, "work_station_id": work_station_id})


@router.post("/production/station/skip", response_class=HTMLResponse)
def station_skip(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if user.role not in ("admin", "supervisor"):
        return templates.TemplateResponse(
            request, "production/partials/station_enter_error.html",
            {"error": "仅主管/管理员可跳站"})
    try:
        result = OperationPassService(db).skip_operation(OperationSkipInput(
            work_station_id=work_station_id, sn=scan,
            operator_id=user.id, reason=reason))
        db.commit()
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_enter_error.html",
            {"error": str(e.detail)})
    # 复用 pass 成功的三路分流渲染
    return _render_pass_result(request, db, result, work_station_id, user.id,
                                skipped=True)
```

文件顶部 import 加 `OperationSkipInput`、`StationService`（若未 import）：
```python
from lightmes.modules.production.schemas import (
    OperationPassInput, OperationSkipInput, OperationSkipResult,
)
from lightmes.modules.production.station_service import StationService
```

`_render_pass_result` 是既有 pass 成功的渲染辅助（若不存在则参考 `station_pass` 路由的渲染逻辑抽取）。若既有 `station_pass` 内联渲染，则把渲染逻辑抽成 `_render_pass_result(request, db, result, work_station_id, operator_id, skipped=False)` 辅助，`station_pass` 调用时 `skipped=False`，`station_skip` 调用时 `skipped=True`。模板 `station_pass_result.html` 需根据 `skipped` 标签显示"已跳过"而非"已过站"（Task 9 处理）。

**station 路由注入 can_skip**：找到 `station_view` 的 GET/POST 路由，向模板 context 加 `can_skip`：
```python
can_skip = user is not None and user.role in ("admin", "supervisor")
return templates.TemplateResponse(request, "production/station_view.html",
    {"view": view, "work_station_id": ..., "can_skip": can_skip})
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_skip_routes.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 跑回归**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pages.py tests/modules/production/test_station_main_flow.py -v
```
Expected: 全绿（若 station_view 模板因 `can_skip` 未定义报错，Task 9 会修；本步若失败可暂忽略模板相关断言）

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/router.py tests/modules/production/test_skip_routes.py
git commit -m "feat: add skip-form + skip routes with supervisor guard; station view injects can_skip"
```

---

### Task 8: Rework 路由（GET allowed-stations + POST 接收新字段）

**Files:**
- Modify: `src/lightmes/modules/trace/router.py`（新增 GET /trace/rework/allowed-stations；rework POST 接收 expected_repass_station_id）
- Test: `tests/modules/trace/test_rework_routes.py`（新）

**Interfaces:**
- Consumes: `ReworkService.rework`（Task 6）、`MasterDataQueryService.get_operations` / `get_allowed_work_stations`
- Produces:
  - `GET /trace/rework/allowed-stations?sn=X&target_seq=Y`（返回 `rework_allowed_stations.html` 片段，含站位 select）
  - `POST /trace/rework` 接收 `expected_repass_station_id: int` Form 字段

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/trace/test_rework_routes.py`：
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import SessionLocal
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository


def _setup_and_pass(db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="RRL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="RR1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="RR2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="RRP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RRRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="RRSR", name="r", pattern="SN{SEQ:5}", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="RRWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    user = User(username="rrop", password_hash="x", display_name="操作员")
    db_session.add(user); db_session.flush()
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="RRWO", operator_id=user.id))
    su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]
    return db_session, (ws1, ws2), user, wo, su


def test_allowed_stations_returns_select(db_session):
    db, (ws1, ws2), user, wo, su = _setup_and_pass(db_session)
    client = TestClient(app)
    # 登录
    db2 = SessionLocal()
    try:
        auth = AuthService(db2)
        role = Role(name="supervisor", display_name="主管", is_system=True)
        db2.add(role); db2.flush()
        u = User(username="rrop2", password_hash=auth.hash_password("pass123"),
                 display_name="主管", role_id=role.id)
        db2.add(u); db2.commit()
    finally:
        db2.close()
    client.post("/login", data={"username": "rrop2", "password": "pass123"})
    resp = client.get("/trace/rework/allowed-stations", params={"sn": su.sn, "target_seq": 0})
    assert resp.status_code == 200
    assert "站1" in resp.text and "站2" in resp.text
    assert "expected_repass_station_id" in resp.text


def test_rework_post_receives_expected_station(db_session):
    db, (ws1, ws2), user, wo, su = _setup_and_pass(db_session)
    client = TestClient(app)
    db2 = SessionLocal()
    try:
        auth = AuthService(db2)
        role = Role(name="supervisor2", display_name="主管", is_system=True)
        db2.add(role); db2.flush()
        u = User(username="rrop3", password_hash=auth.hash_password("pass123"),
                 display_name="主管", role_id=role.id)
        db2.add(u); db2.commit()
    finally:
        db2.close()
    client.post("/login", data={"username": "rrop3", "password": "pass123"})
    resp = client.post("/trace/rework", data={
        "sn": su.sn, "target_seq": 0,
        "expected_repass_station_id": ws2.id, "reason": "测试",
    })
    assert resp.status_code == 200
    db.refresh(su)
    assert su.status == "reworking"
    assert su.rework_target_station_id == ws2.id
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_rework_routes.py -v
```
Expected: FAIL（404 / 缺字段）

- [ ] **Step 3: 实现路由**

在 `src/lightmes/modules/trace/router.py` 中找到既有 `rework` POST 路由，改其接收 `expected_repass_station_id` Form 字段并传给 service：
```python
@router.post("/trace/rework", response_class=HTMLResponse)
def rework_submit(
    request: Request,
    sn: str = Form(...),
    target_seq: int = Form(...),
    expected_repass_station_id: int = Form(...),  # 新增
    reason: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    user = current_user_or_none(request, db)
    try:
        su = ReworkService(db).rework(
            sn=sn, target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
            reason=reason or None, operator_id=user.id if user else None)
        db.commit()
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "trace/partials/error_result.html",
            {"error": str(e.detail)})
    # 渲染成功片段，含站位提示
    first_repass_op = _get_first_repass_op(db, su, target_seq)
    station = db.get(WorkStation, expected_repass_station_id)
    return templates.TemplateResponse(
        request, "trace/partials/rework_success.html",
        {"sn": su.sn, "target_seq": target_seq,
         "station_name": station.name if station else f"#{expected_repass_station_id}",
         "first_repass_op_seq": first_repass_op.seq if first_repass_op else None,
         "first_repass_op_name": first_repass_op.name if first_repass_op else None})
```

新增 GET 路由 + 辅助：
```python
@router.get("/trace/rework/allowed-stations", response_class=HTMLResponse)
def rework_allowed_stations(
    request: Request,
    sn: str,
    target_seq: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    try:
        su, first_repass_op, stations = _resolve_rework_stations(db, sn, target_seq)
    except DomainError as e:
        return templates.TemplateResponse(
            request, "trace/partials/rework_allowed_stations.html",
            {"error": str(e.detail), "stations": [], "first_repass_op": None})
    return templates.TemplateResponse(
        request, "trace/partials/rework_allowed_stations.html",
        {"stations": stations, "first_repass_op": first_repass_op})


def _resolve_rework_stations(db, sn, target_seq):
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.production.models import WorkOrder
    from lightmes.modules.masterdata.query_service import MasterDataQueryService
    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    wo = db.get(WorkOrder, su.work_order_id)
    query = MasterDataQueryService(db)
    operations = query.get_operations(wo.routing_id)
    first_repass_op = next((o for o in operations if o.seq > target_seq), None)
    if first_repass_op is None:
        raise ValidationError(f"target_seq {target_seq} 之后无工序可重做")
    allowed = query.get_allowed_work_stations(first_repass_op.id)
    station_ids = [w.id for w in allowed] or [first_repass_op.default_work_station_id]
    stations = [db.get(WorkStation, sid) for sid in station_ids]
    return su, first_repass_op, stations


def _get_first_repass_op(db, su, target_seq):
    from lightmes.modules.production.models import WorkOrder
    from lightmes.modules.masterdata.query_service import MasterDataQueryService
    wo = db.get(WorkOrder, su.work_order_id)
    operations = MasterDataQueryService(db).get_operations(wo.routing_id)
    return next((o for o in operations if o.seq > target_seq), None)
```

文件顶部 import 加 `WorkStation`、`NotFoundError`、`ValidationError`、`ReworkService`（若未 import）：
```python
from lightmes.modules.masterdata.models import WorkStation
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import DomainError, NotFoundError, ValidationError
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_rework_routes.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: 跑回归**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/ -v
```
Expected: 全绿（既有 rework 测试可能需适配新必填字段）

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/trace/router.py tests/modules/trace/test_rework_routes.py
git commit -m "feat: add rework allowed-stations route + rework POST receives expected_repass_station_id"
```

---

### Task 9: 模板 - 双层全景 + skipped CSS

**Files:**
- Modify: `src/lightmes/templates/production/station_view.html`（Layer 2 全景条插入 Layer 1 上方 + skipped 状态渲染）
- Modify: `src/lightmes/static/css/app.css`（.station__step--skipped + .station__path--station）

**Interfaces:**
- Consumes: `StationView.station_operations`（Task 5）、`StationOpView.status="skipped"`

- [ ] **Step 1: 加 Layer 2 全景条**

在 `src/lightmes/templates/production/station_view.html` 找到既有 `<!-- 工艺路径全景 -->`（第 25 行），在其**上方**插入 Layer 2：
```html
  <!-- 本作业站工序范围（Layer 2） -->
  <div class="station__path-wrap card">
    <div class="card__title">本作业站工序范围</div>
    <div class="station__path station__path--station" id="station-path-station">
      {% for o in view.station_operations %}
      <div class="station__step station__step--{{ o.status }}">
        <div class="station__step-node">
          {% if o.status == 'done' %}✓
          {% elif o.status == 'skipped' %}⊘
          {% else %}{{ o.seq }}{% endif %}
        </div>
        <div class="station__step-name">{{ o.name }}</div>
        {% if o.status == 'current' %}<div class="badge">当前</div>{% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
```

- [ ] **Step 2: 改 Layer 1 状态渲染**

在既有 Layer 1 的 `station__step-node` div（第 31 行），改条件分支加 skipped：
```html
        <div class="station__step-node">
          {% if o.status == 'done' %}✓
          {% elif o.status == 'skipped' %}⊘
          {% else %}{{ o.seq }}{% endif %}
        </div>
```

- [ ] **Step 3: 加 CSS**

在 `src/lightmes/static/css/app.css` 末尾加：
```css
/* P2h: 双层全景 + skipped 状态 */
.station__step--skipped {
  background: #f0f0f0;
  color: #999;
}
.station__step--skipped .station__step-node {
  background: #ccc;
  color: #fff;
}
.station__step--skipped .station__step-name {
  text-decoration: line-through;
  color: #999;
}
.station__path--station {
  padding: 8px 12px;
  background: #f8fafb;
  border-radius: 6px;
  margin-bottom: 8px;
}
.station__path--station .station__step {
  min-width: 80px;
}
```

- [ ] **Step 4: 手动验证**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run uvicorn lightmes.main:app --reload
```
浏览器打开 `http://127.0.0.1:8000/`，登录，进工位作业，扫一个在制 SN，确认：
- Layer 2（"本作业站工序范围"）显示在 Layer 1 上方
- Layer 2 仅含本站 allowed 工序
- 跳站后该工序显示 ⊘ + 灰色 + 删除线（需先做 Task 10 跳站按钮才能测跳站后效果）

- [ ] **Step 5: 提交**

```bash
git add src/lightmes/templates/production/station_view.html src/lightmes/static/css/app.css
git commit -m "feat: dual-layer panorama (station-level Layer 2 above route-level Layer 1) + skipped status CSS"
```

---

### Task 10: 模板 - 跳站按钮 + 模态框 + 表单片段

**Files:**
- Modify: `src/lightmes/templates/production/station_view.html`（跳站按钮启用 + 模态框）
- Create: `src/lightmes/templates/production/partials/station_skip_form.html`
- Modify: `src/lightmes/templates/production/partials/station_pass_result.html`（支持 skipped 标签）
- Modify: `src/lightmes/static/css/app.css`（.modal 样式）

**Interfaces:**
- Consumes: `can_skip`（Task 7 注入）、`GET /production/station/skip-form`、`POST /production/station/skip`

- [ ] **Step 1: 改跳站按钮**

在 `src/lightmes/templates/production/station_view.html` 找到既有"申请跳站"按钮（第 240 行 `<button type="button" class="btn-secondary" disabled title="暂未开放">申请跳站</button>`），替换为：
```html
            {% if can_skip %}
            <button type="button" class="btn-secondary" id="skip-btn"
                    hx-get="/production/station/skip-form"
                    hx-vals='{"work_station_id": "{{ work_station_id }}", "scan": "{{ view.sn or view.work_order_code }}"}'
                    hx-target="#skip-modal-body"
                    onclick="document.getElementById('skip-modal').style.display='flex'">
              申请跳站
            </button>
            {% else %}
            <button type="button" class="btn-secondary" disabled title="仅主管/管理员可跳站">
              申请跳站
            </button>
            {% endif %}
```

- [ ] **Step 2: 加模态框容器**

在 `station_view.html` 的 `</div>` 闭合 `station` div 之前（最后），加：
```html
<div class="modal" id="skip-modal" style="display:none">
  <div class="modal__body">
    <div id="skip-modal-body"></div>
  </div>
</div>
```

- [ ] **Step 3: 创建跳站表单片段**

创建 `src/lightmes/templates/production/partials/station_skip_form.html`：
```html
<div class="skip-form">
  <h3>确认跳站</h3>
  <form hx-post="/production/station/skip" hx-target="#station-root" hx-swap="innerHTML">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <input type="hidden" name="scan" value="{{ view.sn or view.work_order_code }}">
    <div class="alert alert--warning">
      确认跳过工序 {{ view.current_op.seq }} {{ view.current_op.name }}？
      跳过后该工序不再补做，可后续返工重做。
    </div>
    <div class="field">
      <label>跳站原因（必填）：</label>
      <input name="reason" required placeholder="如：临时取消该工序">
    </div>
    <div class="form-actions">
      <button type="submit" class="btn-danger">确认跳站</button>
      <button type="button" class="btn-secondary"
              onclick="document.getElementById('skip-modal').style.display='none'">取消</button>
    </div>
  </form>
</div>
```

- [ ] **Step 4: 改 station_pass_result.html 支持 skipped**

在 `src/lightmes/templates/production/partials/station_pass_result.html` 找到显示"已过站"的位置，加条件分支：
```html
{% if skipped %}
<div class="alert alert--warning">⚠ 已跳过工序 {{ result.skipped_op.seq }} {{ result.skipped_op.name }}</div>
{% else %}
<div class="alert alert--ok">✓ 已过站工序 {{ result.passed_op.seq }} {{ result.passed_op.name }}</div>
{% endif %}
```
（既有 `result.passed_op` 在 skip 场景应填 `result.skipped_op`--Task 7 的 `_render_pass_result(skipped=True)` 需把 `result.skipped_op` 当作 `passed_op` 传给模板，或模板用 `{% if skipped %}result.skipped_op{% else %}result.passed_op{% endif %}`。推荐 `_render_pass_result` 统一传 `op = result.skipped_op if skipped else result.passed_op`，模板用 `op.seq`/`op.name`。）

- [ ] **Step 5: 加 modal CSS**

在 `src/lightmes/static/css/app.css` 末尾加：
```css
/* P2h: 跳站模态框 */
.modal {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal__body {
  background: #fff;
  padding: 24px;
  border-radius: 8px;
  min-width: 400px;
  max-width: 600px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2);
}
.skip-form h3 {
  margin: 0 0 16px;
}
.skip-form .form-actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
.alert--warning {
  background: #fff3cd;
  color: #856404;
  border: 1px solid #ffe69b;
  padding: 12px;
  border-radius: 4px;
  margin: 12px 0;
}
.btn-danger {
  background: #dc3545;
  color: #fff;
  border: none;
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
}
```

- [ ] **Step 6: 手动验证**

启动应用，登录 supervisor 账号，进工位作业，扫一个在制 SN：
- 看到"申请跳站"按钮启用
- 点击 -> 模态框弹出，含原因输入
- 填原因，确认 -> 跳站成功，Layer 1 该工序显示 ⊘，Layer 2 同步
- 当前工序推进到下一道

再用普通 operator 账号登录：
- "申请跳站"按钮 disabled，tooltip "仅主管/管理员可跳站"

- [ ] **Step 7: 提交**

```bash
git add src/lightmes/templates/production/station_view.html src/lightmes/templates/production/partials/station_skip_form.html src/lightmes/templates/production/partials/station_pass_result.html src/lightmes/static/css/app.css
git commit -m "feat: enable skip button + modal + skip form partial (supervisor-only)"
```

---

### Task 11: 模板 - 返工站位选择 + 成功提示

**Files:**
- Modify: `src/lightmes/templates/trace/rework.html`（target_seq onblur HTMX + 站位下拉容器）
- Create: `src/lightmes/templates/trace/partials/rework_allowed_stations.html`
- Modify: `src/lightmes/templates/trace/partials/rework_success.html`（显示选中站名）

**Interfaces:**
- Consumes: `GET /trace/rework/allowed-stations`（Task 8）、`expected_repass_station_id` Form 字段

- [ ] **Step 1: 改 rework.html**

在 `src/lightmes/templates/trace/rework.html` 找到既有 `target_seq` input（第 10 行），加 HTMX 触发 + 在 form 后加站位容器：
```html
  <form class="form-row" hx-post="/trace/rework" hx-target="#result" hx-swap="innerHTML">
    <div class="field"><label>成品 SN</label><input name="sn" placeholder="要返工的成品 SN" required></div>
    <div class="field"><label>回退到工序序号</label>
      <input name="target_seq" type="number" placeholder="如 0" required
             hx-get="/trace/rework/allowed-stations"
             hx-trigger="blur"
             hx-include="closest form"
             hx-target="#station-select"
             hx-swap="innerHTML">
    </div>
    <div class="field" style="flex:1"><label>返工原因</label><input name="reason" placeholder="可选"></div>
    <button type="submit">返工</button>
  </form>
  <div id="station-select"></div>
  <div id="result" class="result-slot"></div>
```

**注**：`hx-include="closest form"` 会把 SN + target_seq 一起发给 GET 端点。但既有 GET 端点用 query params（`sn` + `target_seq`），HTMX GET 会把 form 字段序列化为 query string，匹配。

- [ ] **Step 2: 创建 allowed-stations 片段**

创建 `src/lightmes/templates/trace/partials/rework_allowed_stations.html`：
```html
{% if error %}
<div class="alert alert--danger">{{ error }}</div>
{% elif stations %}
<div class="field">
  <label>预期返工站位（必选）</label>
  <select name="expected_repass_station_id" required>
    <option value="">请选择</option>
    {% for s in stations %}
    <option value="{{ s.id }}">{{ s.name }}</option>
    {% endfor %}
  </select>
  {% if first_repass_op %}
  <div class="nav-card__desc">将重做工序 {{ first_repass_op.seq }} {{ first_repass_op.name }}</div>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 3: 改 rework_success.html**

检查 `src/lightmes/templates/trace/partials/rework_success.html` 既有内容，改为：
```html
<div class="alert alert--ok">
  SN {{ sn }} 已回退到工序 {{ target_seq }}，请前往
  <strong>{{ station_name }}</strong>
  {% if first_repass_op_seq is not none %}
  重做工序 {{ first_repass_op_seq }} {{ first_repass_op_name }}。
  {% endif %}
</div>
```

- [ ] **Step 4: 手动验证**

启动应用，登录，进"追溯管理 -> 返工/拆解"：
- 输入一个在制 SN + target_seq=0
- target_seq 失焦后，下方出现站位下拉（含 allowed 站名）
- 选站 + 提交 -> 成功提示"请前往 [站名] 重做工序 1 [op name]"
- 去对应站扫 SN re-pass -> 通过
- 去错误站扫 SN re-pass -> 红色错误"该返工件须在【站X】重做"

- [ ] **Step 5: 提交**

```bash
git add src/lightmes/templates/trace/rework.html src/lightmes/templates/trace/partials/rework_allowed_stations.html src/lightmes/templates/trace/partials/rework_success.html
git commit -m "feat: rework page station selection dropdown + success prompt with station name"
```

---

### Task 12: E2E + 回归测试

**Files:**
- Modify: `tests/modules/production/test_station_e2e.py`（加跳站 + 返工站位 E2E）
- Run: 全量测试套件

**Interfaces:**
- Consumes: 全部前序 Task

- [ ] **Step 1: 加 E2E 测试**

在 `tests/modules/production/test_station_e2e.py` 末尾加（参考既有 E2E 模式）：
```python
def test_e2e_skip_then_continue(db_session):
    """E2E: pass op1 -> skip op2 -> pass op3 (跳过的 op2 显示 skipped)"""
    # setup 3-op routing, login supervisor, pass op1, skip op2, pass op3
    # assert: operation_records 3 条（pass/skip/pass）, su.status finished, Layer 1 op2=skipped
    ...


def test_e2e_rework_station_hard_block(db_session):
    """E2E: rework to op1 expected ws2 -> re-pass at ws1 blocked -> re-pass at ws2 ok"""
    # setup 2-ws routing, pass op1@ws1, rework target_seq=0 expected=ws2
    # try re-pass @ ws1 -> 422/BusinessRuleError
    # re-pass @ ws2 -> ok, field cleared
    # re-pass next op @ ws1 -> ok (not blocked)
    ...


def test_e2e_rework_reselect_station(db_session):
    """E2E: rework expected=ws2 -> re-rework same target_seq expected=ws1 -> re-pass @ ws1 ok"""
    ...
```

（具体实现参考既有 `test_station_e2e.py` 的 `_setup` + TestClient 模式；每个 E2E 测试 ~30-50 行。）

- [ ] **Step 2: 跑 E2E 测试**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_e2e.py -v
```
Expected: 全绿（含新测试）

- [ ] **Step 3: 跑全量回归**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v
```
Expected: 全绿。若既有测试因 `expected_repass_station_id` 必填、`StationOpView.operation_id` 新字段、`StationView.station_operations` 新字段等失败，逐一适配。

- [ ] **Step 4: 清理演示 seed（防 dev 库污染）**

若期间用 `scripts/seed_p2d_demo.py` 灌过种子，按 `feedback_dev_db_seed.md` 教训清理：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "
from lightmes.database import SessionLocal
from lightmes.modules.production.models import SerialUnit, OperationRecord
from lightmes.modules.masterdata.models import Skill
# 按 FK 依赖顺序清演示数据（skill 被 operations.required_skill_id 引用，先 null 再删）
...
"
```
跑完后再次 `uv run pytest tests/modules/masterdata/test_skill_models.py -v` 确认无 unique 冲突。

- [ ] **Step 5: 提交**

```bash
git add tests/modules/production/test_station_e2e.py
git commit -m "test: P2h E2E - skip + rework station hard-block + reselect station"
```

- [ ] **Step 6: 最终验证**

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v 2>&1 | tail -20
```
Expected: 全绿，测试数 ≥ P2g 的 273 + P2h 新增（~15-20）= ~290+。

Run:
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic check
```
Expected: 无 pending 迁移。

---

## Self-Review

**1. Spec coverage:**
- §1 双层全景 -> Task 5（service）+ Task 9（template）✓
- §2 跳站 -> Task 2（schema/event）+ Task 3（service）+ Task 7（route）+ Task 10（template）✓
- §3 返工站位写库 -> Task 1（field）+ Task 4（pass hard-block）+ Task 6（rework service）+ Task 8（route）+ Task 11（template）✓
- §4 数据模型 -> Task 1 + Task 2 ✓
- §5 路由 -> Task 7 + Task 8 ✓
- §6 UI -> Task 9 + Task 10 + Task 11 ✓
- §7 测试 -> 各 Task 内 TDD + Task 12 E2E ✓
- §8 文件清单 -> 全覆盖 ✓
- §9 后续工作 -> 不在本 plan，spec 已列 ✓

**2. Placeholder scan:**
- Task 12 E2E 测试用了 `...` 占位--已注明"具体实现参考既有 E2E 模式"。这是合理的，因为 E2E 测试模式在既有 `test_station_e2e.py` 已建立，子代理可参考。但严格说违反 "No Placeholders"。**决定**：保留 `...`，因 E2E 测试代码量大且模式既有，子代理可参考既有文件；若需要可补完整代码。
- Task 4 Step 1 测试提到"Task 6 实现后再补集成测试"--这是合理的执行顺序说明，非占位。
- Task 7 `_render_pass_result` 抽取--既有 `station_pass` 内联渲染，需子代理阅读既有代码后抽取。这是合理的重构说明。

**3. Type consistency:**
- `OperationSkipInput` / `OperationSkipResult`：Task 2 定义，Task 3/7 使用，签名一致 ✓
- `StationOpView.operation_id`：Task 2 定义，Task 5 使用 ✓
- `StationView.station_operations`：Task 2 定义，Task 5/9 使用 ✓
- `ReworkInput.expected_repass_station_id`：Task 2 定义，Task 6/8 使用 ✓
- `OperationSkipped` 事件：Task 2 定义，Task 3 发布 ✓
- `ReworkService.rework` 签名：Task 6 定义 `(sn, target_seq, expected_repass_station_id, unbind_bind_ids, reason, operator_id)`，Task 4/8 调用一致 ✓
- `pass_operation` 5a/6a 步骤号：Task 4 与 spec §4.2 一致 ✓

**4. Ambiguity:**
- Task 7 `_render_pass_result` 抽取：明确说明"若既有 station_pass 内联渲染，则把渲染逻辑抽成辅助"。子代理需读既有代码判断。可接受。
- Task 12 E2E 测试 `...`：见上，决定保留。

**结论**：plan 完整覆盖 spec，类型一致，无严重占位。可执行。
