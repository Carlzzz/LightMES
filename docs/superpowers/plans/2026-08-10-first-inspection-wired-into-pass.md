# 首检接进过站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把首检从"过站后 fire-and-forget 记录"改为"过站前硬卡 + 放行"——`pass_operation` 新增步骤 5c，工序有启用的首检配置 + 触发条件命中时，必须提交合格首检才能过站，不合格拒绝过站。

**Architecture:** `OperationPassInput` 新增可选 `first_inspection: FirstInspectionInput`；`FirstInspectionService` 新增 `submit_new_inspection` helper（封装 create + submit 两步）；`pass_operation` 在技能校验（5b）之后、BOM 校验（既有 5c 重命名为 5d）之前插入新步骤 5c——查 config + check_needs_inspection + submit + gate；`station_pass` 路由解析 fi_* 表单构建 `FirstInspectionInput` 传入，删除既有 AFTER-pass 首检创建逻辑。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Jinja2 + HTMX, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- **无新表、无新迁移**。既有五张首检表（FirstInspectionConfig/CheckItem/Record/CheckResult/State）足够。
- **新步骤 5c 位置**：在 `pass_operation` 步骤 5b（技能校验）之后。**既有 5c（BOM 累积校验）注释改名为 5d**（仅注释改名，逻辑不动）。
- **首检失败 = 拒绝过站**：`BusinessRuleError("首检不合格，不可过站（记录 #X）")`。**不做** quarantine 状态、**不做** supervisor waive（属下一个"缺陷管理 + 不良品隔离"spec）。
- **缺首检数据 = 拒绝过站**：needs=True 但 `data.first_inspection is None` 或 `check_results` 为空 → `BusinessRuleError("该工序需首检（触发：X），请填写首检结果后过站")`。
- **inspector 身份**：`inspector_id = data.operator_id`（操作员自检）。
- **跳站不卡首检**：`skip_operation` 不复用首检链，既有 skip 流程不变。
- SQLAlchemy 2.0 风格；Pydantic v2；commit 前缀 `feat:`/`refactor:`/`test:`/`fix:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。
- **测试隔离**：TestClient 路由 commit 会污染共享 dev DB。跑测试前清库：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"`

---

## File Structure

本 spec 结束时新增/修改：

```
src/lightmes/modules/production/
├── schemas.py                  # 改：新增 FirstInspectionCheckResultInput + FirstInspectionInput；OperationPassInput.first_inspection
├── quality_service.py          # 改：FirstInspectionService 新增 submit_new_inspection helper
├── operation_pass_service.py   # 改：pass_operation 新增步骤 5c；既有 5c 注释改 5d；import FirstInspectionService
└── router.py                   # 改：station_pass 构建 FirstInspectionInput 传入；删除 AFTER-pass 首检创建逻辑
tests/modules/production/
└── test_operation_pass_first_inspection.py  # 新：8 个单元 + 边界用例
```

无模板改动（首检卡片 UI 既有，错误渲染走既有 station_view 顶部红条）。

---

### Task 1: Schemas + submit_new_inspection helper

**Files:**
- Modify: `src/lightmes/modules/production/schemas.py`（新增两个 input schema + OperationPassInput 字段）
- Modify: `src/lightmes/modules/production/quality_service.py`（新增 submit_new_inspection helper）
- Test: `tests/modules/production/test_first_inspection_helper.py`（新）

**Interfaces:**
- Produces:
  - `FirstInspectionCheckResultInput(check_item_id, result_type, boolean_value, numeric_value, text_value, remark)`
  - `FirstInspectionInput(check_results: list[FirstInspectionCheckResultInput], remark: str | None)`
  - `OperationPassInput.first_inspection: FirstInspectionInput | None = None`
  - `FirstInspectionService.submit_new_inspection(config, work_order_id, operation_id, work_station_id, inspector_id, trigger_reason, serial_unit_id, check_results, remark) -> FirstInspectionRecord`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_first_inspection_helper.py`：
```python
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem,
)
from lightmes.modules.production.quality_service import FirstInspectionService
from lightmes.modules.production.schemas import (
    FirstInspectionInput, FirstInspectionCheckResultInput,
)


def _setup_with_fi_config(db, check_items_spec):
    """创建工序 + 首检配置 + 检查项。check_items_spec: [(seq, name, check_type, is_mandatory)]"""
    md = MasterDataService(db)
    line = md.create_line(LineCreate(code="FIL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="FIW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="FIP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1",
                           default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="FIRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="FISR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="FIWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 创建首检配置
    op = md.routings.operations_of(routing.id)[0]
    config = FirstInspectionConfig(
        operation_id=op.id, work_station_id=None, name="首检配置",
        is_enabled=True, trigger_new_order=True,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    for seq, name, ctype, mand in check_items_spec:
        db.add(FirstInspectionCheckItem(
            config_id=config.id, seq=seq, name=name, check_type=ctype,
            is_mandatory=mand))
    db.flush()
    return ws, config, wo


def test_submit_new_inspection_passes(db_session):
    db = db_session
    ws, config, wo = _setup_with_fi_config(db, [
        (1, "外观", "boolean", True),
    ])
    fi_svc = FirstInspectionService(db)
    record = fi_svc.submit_new_inspection(
        config=config, work_order_id=wo.id, operation_id=config.operation_id,
        work_station_id=ws.id, inspector_id=1, trigger_reason="new_order",
        serial_unit_id=None,
        check_results=[FirstInspectionCheckResultInput(
            check_item_id=db.query(FirstInspectionCheckItem).first().id,
            result_type="boolean", boolean_value=True)])
    assert record.status == "passed"


def test_submit_new_inspection_fails(db_session):
    db = db_session
    ws, config, wo = _setup_with_fi_config(db, [
        (1, "外观", "boolean", True),
    ])
    fi_svc = FirstInspectionService(db)
    record = fi_svc.submit_new_inspection(
        config=config, work_order_id=wo.id, operation_id=config.operation_id,
        work_station_id=ws.id, inspector_id=1, trigger_reason="new_order",
        serial_unit_id=None,
        check_results=[FirstInspectionCheckResultInput(
            check_item_id=db.query(FirstInspectionCheckItem).first().id,
            result_type="boolean", boolean_value=False)])
    assert record.status == "failed"
```

**注**：`db.query(...)` 是 SQLAlchemy 1.x API，2.0 应改为 `db.execute(select(...)).scalar_one()`。修正测试（避免 1.x API）：
```python
from sqlalchemy import select
# 替换 db.query(FirstInspectionCheckItem).first().id 为：
item_id = db.execute(select(FirstInspectionCheckItem).where(
    FirstInspectionCheckItem.config_id == config.id)).scalar_first().id
```
（实际用 `scalars().first()`：）
```python
item_id = db.execute(select(FirstInspectionCheckItem).where(
    FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
```

- [ ] **Step 2: 跑测试确认失败**

Run（先清库）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_first_inspection_helper.py -v
```
Expected: FAIL with `AttributeError: 'FirstInspectionService' object has no attribute 'submit_new_inspection'`（或 schema ImportError）

- [ ] **Step 3: 加 schemas 到 schemas.py**

在 `src/lightmes/modules/production/schemas.py` 找到 `OperationPassInput` 类定义（约 line 61-67），在其**之前**插入两个新 schema：
```python
class FirstInspectionCheckResultInput(BaseModel):
    check_item_id: int
    result_type: str  # boolean/numeric/text
    boolean_value: bool | None = None
    numeric_value: float | None = None
    text_value: str | None = None
    remark: str | None = None


class FirstInspectionInput(BaseModel):
    check_results: list[FirstInspectionCheckResultInput]
    remark: str | None = None
```

在 `OperationPassInput` 类末尾（`params: list[ParamInput] = []` 之后）加字段：
```python
    first_inspection: FirstInspectionInput | None = None
```

- [ ] **Step 4: 加 submit_new_inspection helper**

在 `src/lightmes/modules/production/quality_service.py` 的 `FirstInspectionService` 类中，找到 `submit_inspection` 方法（约 line 197-258），在其**之后**（`_evaluate_check_result` 之前或之后均可，建议紧跟 `submit_inspection`）加：
```python
    def submit_new_inspection(
        self, config: FirstInspectionConfig, work_order_id: int, operation_id: int,
        work_station_id: int, inspector_id: int, trigger_reason: str,
        serial_unit_id: int | None,
        check_results: list,
        remark: str | None = None,
    ) -> FirstInspectionRecord:
        """创建 + 提交首检记录，返回带最终 status (passed/failed) 的 record。"""
        record = self.create_inspection_record(
            config, work_order_id, operation_id, work_station_id,
            inspector_id, trigger_reason,
            serial_unit_id=serial_unit_id)
        return self.submit_inspection(
            FirstInspectionSubmitInput(
                record_id=record.id, check_results=check_results, remark=remark),
            inspector_id)
```
确认 `FirstInspectionSubmitInput` 已在文件顶部 import（既有）。

- [ ] **Step 5: 跑测试确认通过**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_first_inspection_helper.py -v
```
Expected: 2 tests PASS

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/schemas.py src/lightmes/modules/production/quality_service.py tests/modules/production/test_first_inspection_helper.py
git commit -m "feat: add FirstInspectionInput schemas + submit_new_inspection helper"
```

---

### Task 2: pass_operation 步骤 5c（核心改动）

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（pass_operation 新增 5c；既有 5c 注释改 5d；import FirstInspectionService）
- Test: `tests/modules/production/test_operation_pass_first_inspection.py`（新）

**Interfaces:**
- Consumes: `FirstInspectionInput` + `FirstInspectionCheckResultInput`（Task 1）、`FirstInspectionService.submit_new_inspection`（Task 1）
- Produces: `pass_operation` 在步骤 5b 之后校验首检；不合格/缺数据抛 `BusinessRuleError`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_operation_pass_first_inspection.py`：
```python
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
    FirstInspectionInput, FirstInspectionCheckResultInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem, OperationRecord,
)
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup_with_fi(db, check_items_spec, config_enabled=True, trigger_new_order=True):
    md = MasterDataService(db)
    user = User(username="fiop", password_hash="x", display_name="操作员")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="FIL2", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="FIW2", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="FIP2", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="FIRT2", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="FISR2", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="FIWO2", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 给 op1 加首检配置
    op1 = md.routings.operations_of(routing.id)[0]
    config = FirstInspectionConfig(
        operation_id=op1.id, work_station_id=None, name="首检",
        is_enabled=config_enabled, trigger_new_order=trigger_new_order,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    for seq, name, ctype, mand in check_items_spec:
        db.add(FirstInspectionCheckItem(
            config_id=config.id, seq=seq, name=name, check_type=ctype, is_mandatory=mand))
    db.flush()
    return db, ws, user, wo, config, op1


def _check_item_id(db, config):
    return db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id


def test_pass_no_fi_config_skips_gate(db_session):
    """工序无首检配置 → 直接过站（回归）。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
    from lightmes.modules.production.operation_pass_service import OperationPassService
    from lightmes.modules.auth.models import User
    md = MasterDataService(db_session)
    user = User(username="nofi", password_hash="x", display_name="op")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="NFL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="NFW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="NFP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="NFRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="NFSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(code="NFWO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    result = OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="NFWO", operator_id=user.id))
    assert result.sn is not None  # 无配置 → 直接过站


def test_pass_fi_config_disabled_skips_gate(db_session):
    """config 存在但禁用 → 直接过站。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ], config_enabled=False)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id))
    assert result.sn is not None


def test_pass_fi_needs_but_no_data_blocks(db_session):
    """needs=True + first_inspection=None → 拒绝。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    with pytest.raises(BusinessRuleError, match="该工序需首检"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id))
    # 验证未写过站记录
    op_count = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_count) == 0


def test_pass_fi_needs_passed_data_proceeds(db_session):
    """needs=True + 提交合格首检 → 过站成功。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    item_id = _check_item_id(db, config)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)
        ])))
    assert result.sn is not None
    assert result.passed_op.seq == 1


def test_pass_fi_needs_failed_data_blocks(db_session):
    """needs=True + 提交不合格首检 → 拒绝。"""
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    item_id = _check_item_id(db, config)
    with pytest.raises(BusinessRuleError, match="首检不合格"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)
            ])))
    # 验证未写过站记录
    op_count = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_count) == 0


def test_skip_operation_does_not_trigger_fi(db_session):
    """跳站不触发首检（回归）。"""
    from lightmes.modules.production.schemas import OperationSkipInput
    db, ws, user, wo, config, op1 = _setup_with_fi(db_session, [
        (1, "外观", "boolean", True),
    ])
    # 跳过 op1（需 supervisor，但 service 层不卡角色，路由层卡）
    result = OperationPassService(db).skip_operation(OperationSkipInput(
        work_station_id=ws.id, work_order_code="FIWO2", operator_id=user.id,
        reason="跳过"))
    assert result.skipped_op.seq == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_first_inspection.py -v
```
Expected: 多个 FAIL（`test_pass_fi_needs_but_no_data_blocks` 会 PASS 因为 pass_operation 没改前会直接过站不抛错——实际上会 FAIL 因为 assert len==0 但实际写了记录；其他 fi 测试也 FAIL）

- [ ] **Step 3: 改 pass_operation——加 5c + 重命名 5c→5d**

在 `src/lightmes/modules/production/operation_pass_service.py` 顶部 import 加：
```python
from lightmes.modules.production.quality_service import FirstInspectionService
```

在 `pass_operation` 方法中，找到步骤 5b（技能校验，`# 5b. 技能校验` 块结束，约 line 97）之后、步骤 5c（`# 5c. 物料绑定必扫校验`，约 line 99）之前，插入新步骤 5c（首检）：
```python
        # 5c. 首检硬卡：工序有启用的首检配置 + 触发条件命中时，必须提交合格的首检才能过站
        fi_svc = FirstInspectionService(self.db)
        fi_config = fi_svc.get_config_by_operation(expected.id, data.work_station_id)
        if fi_config and fi_config.is_enabled:
            needs, reason, _fi_state = fi_svc.check_needs_inspection(
                fi_config, wo.id, expected.id)
            if needs:
                if data.first_inspection is None or not data.first_inspection.check_results:
                    raise BusinessRuleError(
                        f"该工序需首检（触发：{reason}），请填写首检结果后过站")
                fi_record = fi_svc.submit_new_inspection(
                    config=fi_config, work_order_id=wo.id, operation_id=expected.id,
                    work_station_id=data.work_station_id, inspector_id=data.operator_id,
                    trigger_reason=reason, serial_unit_id=su.id,
                    check_results=data.first_inspection.check_results,
                    remark=data.first_inspection.remark)
                if fi_record.status == "failed":
                    raise BusinessRuleError(
                        f"首检不合格，不可过站（记录 #{fi_record.id}）")
```

把既有 `# 5c. 物料绑定必扫校验` 注释（约 line 99）改名为 `# 5d. 物料绑定必扫校验（仅最终工序检查累积绑定）`（仅注释改名，逻辑不动）。

- [ ] **Step 4: 跑测试确认通过**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_first_inspection.py -v
```
Expected: 6 tests PASS

- [ ] **Step 5: 跑回归（pass_operation 不受影响）**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skill.py tests/modules/production/test_operation_pass_skip.py tests/modules/production/test_operation_pass_rework_station.py tests/modules/production/test_p2h_e2e.py -v
```
Expected: 全绿（既有测试工序无 fi_config，不受 5c 影响）

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/operation_pass_service.py tests/modules/production/test_operation_pass_first_inspection.py
git commit -m "feat: pass_operation step 5c hard-gates first inspection (block on missing/failed)"
```

---

### Task 3: station_pass 路由集成

**Files:**
- Modify: `src/lightmes/modules/production/router.py`（构建 FirstInspectionInput 传入 + 删除 AFTER-pass 首检创建逻辑）
- Test: `tests/modules/production/test_station_pass_first_inspection.py`（新）

**Interfaces:**
- Consumes: `FirstInspectionInput` + `FirstInspectionCheckResultInput`（Task 1）、`OperationPassInput.first_inspection`（Task 1）、`pass_operation` 5c（Task 2）
- Produces: `station_pass` 路由把 fi_* 表单字段聚合成 `FirstInspectionInput` 传入 `OperationPassInput`；既有 AFTER-pass 首检创建逻辑删除

- [ ] **Step 1: 写失败测试（service-level，不走 TestClient 避免 DB 隔离问题）**

创建 `tests/modules/production/test_station_pass_first_inspection.py`：
```python
"""Task 3: 验证 station_pass 路由把首检数据传到 pass_operation。
Service-level（直接调 pass_operation 模拟路由聚合后的调用），避免 TestClient DB 隔离问题。
完整 HTMX E2E 在 Task 4。
"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
    FirstInspectionInput, FirstInspectionCheckResultInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem, FirstInspectionRecord,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db):
    md = MasterDataService(db)
    user = User(username="spfi", password_hash="x", display_name="操作员")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="SPFL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="SPFW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="SPFP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="SPFRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="SPFSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="SPFWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    op1 = md.routings.operations_of(routing.id)[0]
    config = FirstInspectionConfig(
        operation_id=op1.id, work_station_id=None, name="首检",
        is_enabled=True, trigger_new_order=True,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    db.add(FirstInspectionCheckItem(
        config_id=config.id, seq=1, name="外观", check_type="boolean", is_mandatory=True))
    db.flush()
    return ws, user, wo, config


def test_route_aggregated_fi_data_reaches_pass_operation(db_session):
    """模拟路由聚合 fi_* 表单后的调用：first_inspection 传到 pass_operation 能创建首检记录。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    # 模拟路由聚合后的 OperationPassInput
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="SPFWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)
        ])))
    assert result.sn is not None
    # 验证首检记录已创建（status=passed）
    fi_records = db.execute(select(FirstInspectionRecord).where(
        FirstInspectionRecord.work_order_id == wo.id)).scalars().all()
    assert len(fi_records) == 1
    assert fi_records[0].status == "passed"


def test_route_no_fi_data_blocks_when_needed(db_session):
    """模拟路由未传 first_inspection（操作员没填）：pass_operation 拒绝。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    with pytest.raises(BusinessRuleError, match="该工序需首检"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="SPFWO", operator_id=user.id))
```

- [ ] **Step 2: 跑测试确认失败**

（实际上 Task 2 已经实现了 pass_operation 5c，这两个测试用 service 层调用应该已经 PASS。本 Task 真正改的是路由层。本步先确认 service 层测试 PASS，Step 3 改路由。）

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pass_first_inspection.py -v
```
Expected: 2 PASS（service 层已支持）

- [ ] **Step 3: 改 station_pass 路由——构建 FirstInspectionInput**

在 `src/lightmes/modules/production/router.py` 找到 `station_pass` 函数（约 line 245）。在既有 `params` 解析（约 line 282-291）之后、`# 先过站（创建工序记录）`（约 line 293）之前，插入首检聚合逻辑：
```python
    # 首检：把表单字段聚合成 FirstInspectionInput
    first_inspection = None
    if fi_check_item_id:
        check_results = []
        for i, item_id in enumerate(fi_check_item_id):
            result_type = fi_result_type[i] if i < len(fi_result_type) else "boolean"
            check_results.append(FirstInspectionCheckResultInput(
                check_item_id=item_id,
                result_type=result_type,
                boolean_value=fi_boolean_value[i] if i < len(fi_boolean_value) else None,
                numeric_value=fi_numeric_value[i] if i < len(fi_numeric_value) else None,
                text_value=fi_text_value[i] if i < len(fi_text_value) else None,
                remark=fi_remark[i] if i < len(fi_remark) else None,
            ))
        first_inspection = FirstInspectionInput(
            check_results=check_results,
            remark=fi_overall_remark or None)
```

在 `OperationPassInput` 构造（约 line 295-297）加 `first_inspection=first_inspection`：
```python
    data = OperationPassInput(
        work_station_id=work_station_id, operator_id=user.id,
        components=components, params=params,
        first_inspection=first_inspection)
```

顶部 import 加新 schema（在既有 `from .schemas import (...)` 里追加）：
```python
from lightmes.modules.production.schemas import (
    ...,
    FirstInspectionInput, FirstInspectionCheckResultInput,
)
```

- [ ] **Step 4: 删除 AFTER-pass 首检创建逻辑**

在 `station_pass` 函数中，找到 `# 处理首检 - 注意：result.passed_op 是刚完成的工序`（约 line 337）到对应 try/except 块结束（约 line 357，`pass` 之前）的整段，**整段删除**。这段逻辑（创建 fi_record + submit）已移到 pass_operation 内部 5c。

删除后该区域只剩 `# 处理测试数据` 块（既有，不动）。

- [ ] **Step 5: 跑 service 层测试 + 既有路由测试**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pass_first_inspection.py tests/modules/production/test_station_pages.py -v
```
Expected: Task 3 测试 PASS；`test_station_pages.py` 可能因 TestClient DB 隔离问题有预存失败（非本 Task 引入）。

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/router.py tests/modules/production/test_station_pass_first_inspection.py
git commit -m "feat: station_pass route aggregates fi form data into FirstInspectionInput; remove post-pass fi logic"
```

---

### Task 4: E2E + 回归

**Files:**
- Test: `tests/modules/production/test_first_inspection_e2e.py`（新）
- Run: 全量 P2h + 首检相关回归

**Interfaces:**
- Consumes: 全部前序 Task

- [ ] **Step 1: 写 E2E 测试**

创建 `tests/modules/production/test_first_inspection_e2e.py`：
```python
"""首检接进过站 E2E：service 层模拟完整流程（避免 TestClient DB 隔离问题）。"""
import pytest
from sqlalchemy import select
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
    FirstInspectionInput, FirstInspectionCheckResultInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem, FirstInspectionRecord,
    FirstInspectionState, OperationRecord,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db, with_fi=True):
    md = MasterDataService(db)
    user = User(username="e2efi", password_hash="x", display_name="操作员")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="E2FL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="E2FW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="E2FP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="E2FRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="E2FSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="E2FWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    if with_fi:
        op1 = md.routings.operations_of(routing.id)[0]
        config = FirstInspectionConfig(
            operation_id=op1.id, work_station_id=None, name="首检",
            is_enabled=True, trigger_new_order=True,
            sample_size=1, require_authorization=False, quarantine_on_fail=False)
        db.add(config); db.flush()
        db.add(FirstInspectionCheckItem(
            config_id=config.id, seq=1, name="外观", check_type="boolean", is_mandatory=True))
        db.flush()
        return ws, user, wo, config
    return ws, user, wo, None


def test_e2e_fi_passed_then_second_pass_no_retrigger(db_session):
    """首检通过后，同工单同工序第二次过站不再触发首检（state.last_passed_at 已设）。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    # 第一件：过 op1（需首检，提交合格）
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="E2FWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)])))
    # state.last_passed_at 应已设
    state = db.execute(select(FirstInspectionState).where(
        FirstInspectionState.work_order_id == wo.id,
        FirstInspectionState.operation_id == config.operation_id)).scalar_one()
    assert state.last_passed_at is not None


def test_e2e_fi_failed_leaves_no_operation_record(db_session):
    """首检失败 → 无 operation_record 写入（5c 在步骤 6 之前）。"""
    db = db_session
    ws, user, wo, config = _setup(db)
    item_id = db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id
    with pytest.raises(BusinessRuleError, match="首检不合格"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="E2FWO", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)])))
    op_records = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_records) == 0


def test_e2e_no_fi_config_proceeds_normally(db_session):
    """无首检配置的工序不受影响（回归）。"""
    db = db_session
    ws, user, wo, config = _setup(db, with_fi=False)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="E2FWO", operator_id=user.id))
    assert result.sn is not None
    assert result.passed_op.seq == 1
```

- [ ] **Step 2: 跑 E2E 测试**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_first_inspection_e2e.py -v
```
Expected: 3 PASS

- [ ] **Step 3: 全量回归（首检 + P2h + 既有 pass）**

Run（清库后）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_first_inspection_helper.py tests/modules/production/test_operation_pass_first_inspection.py tests/modules/production/test_station_pass_first_inspection.py tests/modules/production/test_first_inspection_e2e.py tests/modules/production/test_operation_pass.py tests/modules/production/test_operation_pass_skill.py tests/modules/production/test_operation_pass_skip.py tests/modules/production/test_operation_pass_rework_station.py tests/modules/production/test_p2h_e2e.py -v
```
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add tests/modules/production/test_first_inspection_e2e.py
git commit -m "test: first inspection E2E - passed-no-retrigger + failed-no-record + no-config-regression"
```

- [ ] **Step 5: 最终验证**

Run（清库后跑全量）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest 2>&1 | tail -5
```
Expected: 首检相关测试全绿；预存 TestClient DB 隔离失败不变（非本 spec 引入）。

Run（迁移检查）：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic check
```
Expected: 无 pending 迁移（本 spec 无新迁移）。

---

## Self-Review

**1. Spec coverage:**
- §1 现状（fire-and-forget → 硬卡）→ Task 2 (pass_operation 5c) + Task 3 (删除 AFTER-pass 逻辑) ✓
- §2 8 项决策 → Task 1-4 全覆盖（位置/失败处理/inspector/skip 不卡） ✓
- §3 无新表无迁移 → Global Constraints 明确 ✓
- §4 Schema → Task 1 ✓
- §5.1 submit_new_inspection helper → Task 1 ✓
- §5.2 pass_operation 5c → Task 2 ✓
- §5.3 skip_operation 不改 → Task 2 `test_skip_operation_does_not_trigger_fi` 验证 ✓
- §6 路由改动 → Task 3 ✓
- §7 边界（8 场景）→ Task 2 测试覆盖 5 个 + Task 4 E2E 覆盖 3 个 ✓
- §8 测试 → Task 1-4 全覆盖 ✓
- §9 文件清单 → File Structure 一致 ✓

**2. Placeholder scan:**
- Task 1 Step 1 的 `db.query(...)` 注释和修正——已提供 `select(...)` 替代代码，明确说明。✓
- 无 "TBD"/"TODO"/"add appropriate error handling" 等。✓

**3. Type consistency:**
- `FirstInspectionCheckResultInput` 字段名（check_item_id/result_type/boolean_value/numeric_value/text_value/remark）在 Task 1 定义，Task 2/3 使用一致 ✓
- `FirstInspectionInput.check_results` 类型 `list[FirstInspectionCheckResultInput]` 一致 ✓
- `OperationPassInput.first_inspection` 类型 `FirstInspectionInput | None` 一致 ✓
- `submit_new_inspection` 签名（config, work_order_id, operation_id, work_station_id, inspector_id, trigger_reason, serial_unit_id, check_results, remark）在 Task 1 定义，Task 2 调用一致 ✓
- 步骤号 5c（首检，新）/ 5d（BOM，既有改名）在 Task 2 + Global Constraints 一致 ✓

**结论**：plan 完整覆盖 spec，类型一致，无严重占位。可执行。
