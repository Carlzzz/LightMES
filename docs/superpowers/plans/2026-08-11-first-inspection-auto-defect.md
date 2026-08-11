# 首检 failed 自动建缺陷 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首检失败时自动创建 DefectRecord + 隔离 SN + 保留 FirstInspectionRecord 审计——衔接首检与缺陷管理两个已完成 spec。

**Architecture:** `DefectService` 新增 `ensure_system_defect_types`（启动幂等创建 FIRST_INSPECTION_FAIL 系统类型）+ `log_defect_from_inspection`（复用既有 log_defect）；`pass_operation` 5c failed 分支扩展：log_defect_from_inspection + `db.commit()`（保留 fi_record + defect + quarantined SN）+ raise；`main.py` startup 调 ensure。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试命令用 `127.0.0.1`：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- **无新表、无迁移**。DefectType 表既有；启动时 insert 系统类型行（幂等）。
- **系统缺陷类型**：code=`FIRST_INSPECTION_FAIL`，name=`首检不合格`，category=`质量`，severity=`critical`。
- **事务策略**：5c failed 时 `db.commit()` 保留 fi_record + defect + quarantined SN，**再** raise BusinessRuleError。路由 catch + rollback 无 pending 可回滚。
- **错误消息**：`f"首检不合格，SN 已隔离，缺陷记录 #{defect.id}。请前往 /quality/defects/{defect.id} 处理。"`——含缺陷记录号 + 详情链接。
- **discoverer**：`data.operator_id`（提交首检的操作员）。
- **remark**：`f"首检不合格（触发：{reason}）"`——带首检触发原因。
- SQLAlchemy 2.0 风格；commit 前缀 `feat:`/`fix:`/`test:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。
- **测试隔离**：跑测试前清库：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"`

---

## File Structure

```
src/lightmes/modules/production/
├── defect_service.py            # 改：SYSTEM_DEFECT_TYPES + _get_or_create_system_defect_type + ensure_system_defect_types + log_defect_from_inspection
├── operation_pass_service.py    # 改：5c failed 分支（commit + defect + raise）+ import DefectService
src/lightmes/main.py             # 改：startup 调 ensure_system_defect_types
tests/modules/production/
├── test_first_inspection_auto_defect.py  # 新：5 用例
└── test_operation_pass_first_inspection.py  # 改：适配新错误消息 + SN quarantined 断言
```

---

### Task 1: DefectService 系统类型 + log_defect_from_inspection

**Files:**
- Modify: `src/lightmes/modules/production/defect_service.py`
- Test: `tests/modules/production/test_defect_service_auto_defect.py`（新）

**Interfaces:**
- Consumes: 既有 `DefectService.log_defect`、`DefectType` model
- Produces:
  - `DefectService._get_or_create_system_defect_type(code, name, severity, category, description=None) -> DefectType`
  - `DefectService.ensure_system_defect_types() -> None`
  - `DefectService.log_defect_from_inspection(fi_record, sn, discovered_by, remark=None) -> DefectRecord`
  - module constant `SYSTEM_DEFECT_TYPES: list[dict]`

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/production/test_defect_service_auto_defect.py`：
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
from lightmes.modules.production.models import DefectType, DefectRecord, FirstInspectionRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User


def _setup_with_fi(db):
    md = MasterDataService(db)
    user = User(username="adop", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="ADL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="ADW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="ADP", name="件", type="finished"))
    ops = [OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]
    routing = md.create_routing(RoutingCreate(code="ADRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="ADSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="ADWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    op1 = md.routings.operations_of(routing.id)[0]
    from lightmes.modules.production.models import FirstInspectionConfig, FirstInspectionCheckItem
    config = FirstInspectionConfig(
        operation_id=op1.id, work_station_id=None, name="首检",
        is_enabled=True, trigger_new_order=True,
        sample_size=1, require_authorization=False, quarantine_on_fail=False)
    db.add(config); db.flush()
    db.add(FirstInspectionCheckItem(
        config_id=config.id, seq=1, name="外观", check_type="boolean", is_mandatory=True))
    db.flush()
    return ws, user, wo, config


def test_ensure_system_defect_types_idempotent(db_session):
    svc = DefectService(db_session)
    svc.ensure_system_defect_types()
    svc.ensure_system_defect_types()  # 再调一次
    count = db_session.execute(select(DefectType).where(
        DefectType.code == "FIRST_INSPECTION_FAIL")).scalars().all()
    assert len(count) == 1
    assert count[0].severity == "critical"
    assert count[0].category == "质量"
    assert count[0].is_active is True


def test_log_defect_from_inspection_creates_defect(db_session):
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="ADWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 构造一个 failed fi_record（手动创建，模拟 submit_new_inspection 失败结果）
    fi_record = FirstInspectionRecord(
        config_id=config.id, work_order_id=wo.id, operation_id=config.operation_id,
        work_station_id=ws.id, serial_unit_id=su.id, trigger_reason="new_order",
        inspector_id=user.id, status="failed")
    db.add(fi_record); db.flush()
    defect = DefectService(db).log_defect_from_inspection(
        fi_record=fi_record, sn=su.sn, discovered_by=user.id,
        remark="首检不合格（触发：new_order）")
    db.refresh(su)
    assert defect.defect_type_code == "FIRST_INSPECTION_FAIL"
    assert defect.severity == "critical"
    assert defect.remark == "首检不合格（触发：new_order）"
    assert defect.operation_id == config.operation_id
    assert su.status == "quarantined"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_service_auto_defect.py -v
```
Expected: FAIL with `AttributeError: 'DefectService' object has no attribute 'ensure_system_defect_types'`

- [ ] **Step 3: 实现 DefectService 新方法**

在 `src/lightmes/modules/production/defect_service.py` 顶部（class 外，import 之后）加常量：
```python
SYSTEM_DEFECT_TYPES = [
    {"code": "FIRST_INSPECTION_FAIL", "name": "首检不合格",
     "category": "质量", "severity": "critical",
     "description": "系统自动创建：首检不合格"},
]
```

在 `DefectService` 类中（建议紧跟 `__init__` 之后，`log_defect` 之前）加：
```python
    def _get_or_create_system_defect_type(self, code: str, name: str,
                                           severity: str, category: str,
                                           description: str | None = None) -> DefectType:
        """获取或创建系统缺陷类型（幂等；强制 is_active=True 防止管理员误停用）。"""
        dt = self.db.execute(
            select(DefectType).where(DefectType.code == code)
        ).scalar_one_or_none()
        if dt is None:
            dt = DefectType(code=code, name=name, category=category,
                            severity=severity, description=description, is_active=True)
            self.db.add(dt); self.db.flush()
        elif not dt.is_active:
            dt.is_active = True  # 防误停用
        return dt

    def ensure_system_defect_types(self) -> None:
        """启动时调用：幂等创建系统缺陷类型。"""
        for spec in SYSTEM_DEFECT_TYPES:
            self._get_or_create_system_defect_type(**spec)
        self.db.flush()
```

在 `DefectService` 类末尾（`handle_concession` 之后）加：
```python
    def log_defect_from_inspection(self, fi_record, sn: str, discovered_by: int,
                                    remark: str | None = None) -> DefectRecord:
        """首检失败时调用：用系统 FIRST_INSPECTION_FAIL 类型 + 既有 log_defect。"""
        dt = self._get_or_create_system_defect_type(
            code="FIRST_INSPECTION_FAIL", name="首检不合格",
            severity="critical", category="质量",
            description="系统自动创建：首检不合格")
        return self.log_defect(
            defect_type_id=dt.id, sn=sn, discovered_by=discovered_by,
            operation_id=fi_record.operation_id,
            work_station_id=fi_record.work_station_id,
            position=None, remark=remark)
```

顶部 import 加 `select`（若未 import）：
```python
from sqlalchemy import select
```

- [ ] **Step 4: 跑测试确认通过**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_service_auto_defect.py -v
```
Expected: 2 PASS

- [ ] **Step 5: 跑既有 DefectService 回归**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_service.py tests/modules/production/test_defect_models.py -v
```
Expected: 全绿（既有测试不受新方法影响）

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/defect_service.py tests/modules/production/test_defect_service_auto_defect.py
git commit -m "feat: DefectService system types + log_defect_from_inspection (FIRST_INSPECTION_FAIL)"
```

---

### Task 2: pass_operation 5c failed 分支 + main.py startup + 既有测试适配

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（5c failed 分支扩展 + import DefectService）
- Modify: `src/lightmes/main.py`（startup 加 ensure_system_defect_types）
- Modify: `tests/modules/production/test_operation_pass_first_inspection.py`（适配新错误消息 + SN quarantined 断言）

**Interfaces:**
- Consumes: `DefectService.log_defect_from_inspection`（Task 1）
- Produces: pass_operation 5c failed → defect + commit + raise；启动 ensure 系统类型

- [ ] **Step 1: 改 pass_operation 5c failed 分支**

在 `src/lightmes/modules/production/operation_pass_service.py` 顶部 import 加：
```python
from lightmes.modules.production.defect_service import DefectService
```

找到 5c 的 failed 分支（既有，约 line 120-122）：
```python
                if fi_record.status == "failed":
                    raise BusinessRuleError(
                        f"首检不合格，不可过站（记录 #{fi_record.id}）")
```

改为：
```python
                if fi_record.status == "failed":
                    defect = DefectService(self.db).log_defect_from_inspection(
                        fi_record=fi_record, sn=su.sn,
                        discovered_by=data.operator_id,
                        remark=f"首检不合格（触发：{reason}）")
                    self.db.commit()  # 保留 fi_record + defect + quarantined SN
                    raise BusinessRuleError(
                        f"首检不合格，SN 已隔离，缺陷记录 #{defect.id}。"
                        f"请前往 /quality/defects/{defect.id} 处理。")
```

- [ ] **Step 2: 改 main.py startup**

在 `src/lightmes/main.py` 找到 `on_startup` 函数（约 line 59-72），在 `AuthService(db).ensure_admin_user()` 之后、`finally: db.close()` 之前加：
```python
        from lightmes.modules.production.defect_service import DefectService
        DefectService(db).ensure_system_defect_types()
```

完整 on_startup 应为：
```python
@app.on_event("startup")
def on_startup():
    """应用启动时初始化数据库和默认数据"""
    Base.metadata.create_all(bind=engine)
    from lightmes.database import SessionLocal
    from lightmes.modules.auth.service import AuthService
    from lightmes.modules.production.defect_service import DefectService
    db = SessionLocal()
    try:
        AuthService(db).ensure_admin_user()
        DefectService(db).ensure_system_defect_types()
        db.commit()
    finally:
        db.close()
```

**注**：若 `ensure_admin_user` 内部已 commit，`db.commit()` 在 finally 前仍安全（无 pending 时 no-op）。若 ensure_admin_user 仅 flush，则需显式 commit 让 ensure_system_defect_types 的结果持久化。检查 `ensure_admin_user` 实现：它调用 `self.db.flush()`（不 commit）。所以 on_startup 需 `db.commit()` 收尾。

- [ ] **Step 3: 适配既有首检测试**

打开 `tests/modules/production/test_operation_pass_first_inspection.py`，找到 `test_pass_fi_needs_failed_data_blocks`（既有，断言首检不合格拒绝过站）。原断言：
```python
    with pytest.raises(BusinessRuleError, match="首检不合格"):
        ...
    # 验证未写过站记录
    op_count = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_count) == 0
```

改为（新行为：SN 被隔离 + 缺陷记录创建）：
```python
    with pytest.raises(BusinessRuleError, match="首检不合格.*缺陷记录 #"):
        ...
    # 验证未写过站记录（5c 在步骤 6 之前）
    op_count = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_count) == 0
    # 新：SN 被隔离 + 缺陷记录创建
    db.refresh(su)
    assert su.status == "quarantined"
    from lightmes.modules.production.models import DefectRecord
    defects = db.execute(select(DefectRecord).where(
        DefectRecord.serial_unit_id == su.id)).scalars().all()
    assert len(defects) == 1
    assert defects[0].defect_type_code == "FIRST_INSPECTION_FAIL"
```

**注**：`su` 变量需在该测试中可访问。检查既有测试是否已有 `su = ...`，若无则在 `with pytest.raises` 之前加 `su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]`。

- [ ] **Step 4: 跑既有首检测试**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_pass_first_inspection.py -v
```
Expected: 6 PASS（含被适配的 failed 测试 + 其他 5 个回归）

- [ ] **Step 5: 跑首检 E2E 回归**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_first_inspection_e2e.py -v
```
Expected: 3 PASS（既有 E2E：passed-no-retrigger / failed-no-record / no-config-regression。注意 `test_e2e_fi_failed_leaves_no_operation_record` 仍应通过——它只断言 operation_record 数量为 0，不关心 defect_record）

- [ ] **Step 6: 提交**

```bash
git add src/lightmes/modules/production/operation_pass_service.py src/lightmes/main.py tests/modules/production/test_operation_pass_first_inspection.py
git commit -m "feat: pass_operation 5c failed auto-creates defect + quarantines SN + preserves audit"
```

---

### Task 3: 自动建缺陷 E2E + 全量回归

**Files:**
- Test: `tests/modules/production/test_first_inspection_auto_defect_e2e.py`（新）

**Interfaces:**
- Consumes: 全部前序 Task

- [ ] **Step 1: 写 E2E 测试**

创建 `tests/modules/production/test_first_inspection_auto_defect_e2e.py`：
```python
"""首检 failed 自动建缺陷 E2E：完整流程验证。"""
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
    FirstInspectionConfig, FirstInspectionCheckItem,
    FirstInspectionRecord, DefectRecord, OperationRecord,
)
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup_with_fi(db):
    md = MasterDataService(db)
    user = User(username="ade2", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="AEL", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="AEW", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="AEP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=1, code="OP1", name="工序1", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
        OperationCreate(seq=2, code="OP2", name="工序2", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="AERT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="AESR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="AEWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
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


def _check_item_id(db, config):
    return db.execute(select(FirstInspectionCheckItem).where(
        FirstInspectionCheckItem.config_id == config.id)).scalars().first().id


def test_e2e_fi_failed_auto_creates_defect_and_quarantines(db_session):
    """首检不合格 → 自动建缺陷 + 隔离 SN + 拒绝过站 + 保留 fi_record。"""
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    item_id = _check_item_id(db, config)
    with pytest.raises(BusinessRuleError, match="首检不合格.*缺陷记录 #"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="AEWO", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)
            ])))
    # SN 被隔离
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    db.refresh(su)
    assert su.status == "quarantined"
    # 缺陷记录创建
    defects = db.execute(select(DefectRecord).where(
        DefectRecord.serial_unit_id == su.id)).scalars().all()
    assert len(defects) == 1
    assert defects[0].defect_type_code == "FIRST_INSPECTION_FAIL"
    assert defects[0].severity == "critical"
    assert "new_order" in defects[0].remark
    # FirstInspectionRecord 保留（status=failed）
    fi_records = db.execute(select(FirstInspectionRecord).where(
        FirstInspectionRecord.serial_unit_id == su.id)).scalars().all()
    assert len(fi_records) == 1
    assert fi_records[0].status == "failed"
    # 无 operation_record（过站未推进）
    op_records = db.execute(select(OperationRecord).where(
        OperationRecord.work_order_id == wo.id)).scalars().all()
    assert len(op_records) == 0


def test_e2e_fi_failed_then_repass_blocked_by_quarantine(db_session):
    """首检不合格隔离后，再扫该 SN 过站被拒（已隔离）。"""
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    item_id = _check_item_id(db, config)
    # 第一次：提交不合格首检 → 隔离
    with pytest.raises(BusinessRuleError):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, work_order_code="AEWO", operator_id=user.id,
            first_inspection=FirstInspectionInput(check_results=[
                FirstInspectionCheckResultInput(
                    check_item_id=item_id, result_type="boolean", boolean_value=False)
            ])))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    # 第二次：再扫过站 → 被隔离拒绝
    with pytest.raises(BusinessRuleError, match="已quarantined"):
        OperationPassService(db).pass_operation(OperationPassInput(
            work_station_id=ws.id, sn=su.sn, operator_id=user.id))


def test_e2e_fi_passed_no_defect(db_session):
    """首检合格 → 无缺陷记录（回归）。"""
    db = db_session
    ws, user, wo, config = _setup_with_fi(db)
    item_id = _check_item_id(db, config)
    result = OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws.id, work_order_code="AEWO", operator_id=user.id,
        first_inspection=FirstInspectionInput(check_results=[
            FirstInspectionCheckResultInput(
                check_item_id=item_id, result_type="boolean", boolean_value=True)
        ])))
    assert result.sn is not None
    su = SerialUnitRepository(db).get_by_sn(result.sn)
    defects = db.execute(select(DefectRecord).where(
        DefectRecord.serial_unit_id == su.id)).scalars().all()
    assert len(defects) == 0
```

- [ ] **Step 2: 跑 E2E**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_first_inspection_auto_defect_e2e.py -v
```
Expected: 3 PASS

- [ ] **Step 3: 全量回归**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python -c "from sqlalchemy import create_engine, text; e = create_engine('postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes'); c = e.connect(); c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\")); tables = [r[0] for r in c.execute(text(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'\"))]; c.execute(text('TRUNCATE TABLE ' + ', '.join(tables) + ' RESTART IDENTITY CASCADE')); c.commit()"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_defect_service_auto_defect.py tests/modules/production/test_first_inspection_auto_defect_e2e.py tests/modules/production/test_operation_pass_first_inspection.py tests/modules/production/test_first_inspection_e2e.py tests/modules/production/test_defect_service.py tests/modules/production/test_defect_e2e.py tests/modules/production/test_operation_pass.py tests/modules/production/test_p2h_e2e.py -v
```
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add tests/modules/production/test_first_inspection_auto_defect_e2e.py
git commit -m "test: first-inspection-fail auto-defect E2E (defect+quarantine+audit preserved)"
```

- [ ] **Step 5: 最终验证（迁移检查）**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic check
```
Expected: 无 pending 迁移（本 spec 无新迁移）。

---

## Self-Review

**1. Spec coverage:**
- §1 现状（fire-and-forget → 硬卡）→ Task 2 (pass_operation 5c failed 扩展) ✓
- §2 7 项决策 → 全体现（#2 commit-before-raise 在 Task 2；#1 ensure 在 Task 1+2 startup）✓
- §3 数据流 → Task 2 Step 1 代码一致 ✓
- §4.1 DefectService 新方法 → Task 1 ✓
- §4.2 pass_operation 5c → Task 2 ✓
- §4.3 main.py startup → Task 2 Step 2 ✓
- §5 边界（8 场景）→ Task 3 E2E 覆盖关键路径（failed→隔离→再过站拒；passed→无缺陷）；既有"让步/返工/报废"处理已在缺陷管理 spec 验证 ✓
- §6 测试 → Task 1（2 单元）+ Task 2（既有适配）+ Task 3（3 E2E）✓
- §7 文件清单 → File Structure 一致 ✓

**2. Placeholder scan:**
- 无 "TBD"/"TODO"。
- Task 2 Step 3 适配代码完整（含 su 变量获取说明）。
- Task 3 E2E 代码完整。

**3. Type consistency:**
- `log_defect_from_inspection(fi_record, sn, discovered_by, remark)` 签名在 Task 1 定义，Task 2 调用一致 ✓
- `ensure_system_defect_types()` 签名一致 ✓
- `_get_or_create_system_defect_type(code, name, severity, category, description)` 签名一致 ✓
- `SYSTEM_DEFECT_TYPES` 字段名（code/name/category/severity/description）跨 Task 一致 ✓
- `FIRST_INSPECTION_FAIL` code 字符串跨 Task 一致 ✓

**结论**：plan 完整覆盖 spec，类型一致，无严重占位。可执行。
