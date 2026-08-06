# P2e SN 生命周期重构 + 载体码过站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 SN 从"首次过站惰性生成"重构为"工单下达时批量预生成（status=pending）"，引入载体码（托盘/来料唯一码）作为 SN 标签打印前的过渡标识，首站先选工单再扫载体码顺序投产。

**Architecture:** 方案 A——下达时批量预建 SerialUnit（唯一身份载体）；carrier_code 存 SerialUnit 字段（直读）+ 独立 carrier_binding 历史表（追溯/解绑）。载体码活跃唯一用部分唯一索引；后续站扫码自动识别 SN 或载体码。pending 单元不进 WIP/追溯现场视图。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0：`Mapped[]`/`mapped_column()`，继承 `Base`+`TimestampMixin`。所有 schema 变更走 Alembic；autogenerate 后**打开迁移确认只动预期表/索引**，不得误删既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component/uq_operator_skill_user_skill）。
- **预生成时机 = 工单 release 时**，按 qty 批量建 SerialUnit（status=`pending`, current_operation_seq=0, carrier_code=None），SN 号用现有 `SnGenerator.next_sn(rule)` 循环取（不改 SnGenerator）。
- **载体码同时唯一、解绑可复用**：SerialUnit.carrier_code 部分唯一索引（`carrier_code IS NOT NULL` 时 unique）；解绑=置 None + 填 carrier_binding.unbound_at。
- **扫码自动识别**：先按 SN 查，查不到再按 carrier_code 查活跃 SerialUnit（`carrier_code=:scan AND status NOT IN ('finished','scrapped')`）。
- **pending 单元过站**：视同待过首工序（seq=0），过站时 status pending→in_process。
- **work_order_code 首件分支**：不再新生成 SN，改取该工单第一个 pending SerialUnit（不超 qty）；无 pending → BusinessRuleError。
- **pending 过滤**：WIP 看板 / 追溯 SerialUnit 列表默认排除 status=`pending`。
- **首站工单校验**：仅 `status IN ('released','in_process')` 且 line_id=本作业站产线 的工单可投产。
- **解绑角色钩子**：本期任何登录用户可解绑；unbind 服务方法接收 operator_id，保留显式"权限校验钩子"占位注释（形如 P2c 技能钩子），不硬编码放行分支逻辑。
- **operator_id 服务端赋值**（防伪造，沿用 P2d）；写操作 require_login（页面 `current_user_or_none`→401+HX-Redirect `/login`）；HTMX `{{ }}` 自动转义，手写片段用 `markupsafe.escape`。
- 领域异常全局 handler（DomainError 基类统一）；事务边界 get_db；repository 只 flush。
- 提交前缀 `feat:`/`refactor:`/`test:`/`chore:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

P2e 结束时新增/修改：

```
src/lightmes/modules/production/
├── models.py            # 改：SerialUnit 加 carrier_code + 部分唯一索引 uq_active_carrier；加 CarrierBinding 模型
├── repository.py        # 改：SerialUnitRepository 加 first_pending_by_work_order/get_active_by_carrier/count_pending_by_work_order；WorkOrderRepository 加 selectable_for_station；加 CarrierBindingRepository
├── schemas.py           # 改：加 CarrierBindInput/CarrierUnbindInput；StationView 加 remaining_pending/is_first_station 等首站字段（见 Task 5）
├── service.py           # 改：release_work_order 批量预生成 + qty/sn_rule 校验
├── operation_pass_service.py  # 改：载体码定位 + pending→in_process + work_order_code 首件取 pending
├── carrier_service.py   # 新：CarrierService（bind_and_pass_first / unbind）
├── wip_service.py       # 已排除非 in_process（pending 天然不显示，加回归测试即可）
└── router.py            # 改：/production/station/select-wo + /bind-and-pass 首站路由
src/lightmes/modules/trace/
├── trace_service.py     # 改：SerialUnit 查询排除 pending（如有列表查询）；解绑服务或复用 CarrierService
└── router.py            # 改：/trace/carrier-unbind 页面
src/lightmes/migrations/versions/  # 新：carrier_code 列 + uq_active_carrier + carrier_binding 表
src/lightmes/templates/production/
├── station.html         # 改：首站"先选工单"流
├── station_view.html    # 复用（后续站不变）
└── partials/            # 新：station_wo_selected.html（选中工单+扫载体码）、station_bind_result.html（投产结果/用完提示）
src/lightmes/templates/trace/carrier_unbind.html  # 新：解绑页
src/lightmes/templates/home.html  # 改：移除 /production/scan 卡片；加 /trace/carrier-unbind 入口（可选）
tests/modules/production/  # 预生成 / 载体码定位 / CarrierService / 首站流 测试
tests/modules/trace/       # 解绑页测试
```

---

### Task 1: 数据模型 SerialUnit.carrier_code + CarrierBinding 表 + 迁移

**Files:**
- Modify: `src/lightmes/modules/production/models.py`
- Test: `tests/modules/production/test_carrier_models.py`
- Create: `src/lightmes/migrations/versions/<auto>_add_carrier_code_and_binding.py`

**Interfaces:**
- Produces:
  - `SerialUnit.carrier_code: Mapped[str | None]`（default None）+ 部分唯一索引 `uq_active_carrier`（`carrier_code IS NOT NULL` 时 unique）
  - `CarrierBinding`（table `carrier_binding`）: `id` PK, `serial_unit_id: int` FK serial_units.id, `carrier_code: str`, `bound_at: datetime`(server_default now), `unbound_at: datetime | None`(default None), `operator_id: int | None` FK users.id, + TimestampMixin

- [ ] **Step 1: 加 carrier_code 字段 + 部分唯一索引**

在 `production/models.py`。确认顶部 import 有 `Index`, `text`（当前文件已 import `DateTime, ForeignKey, func`——需补 `Index, text`）。改 `SerialUnit`：给类加 `__table_args__` 与 carrier_code 列：
```python
class SerialUnit(Base, TimestampMixin):
    __tablename__ = "serial_units"
    __table_args__ = (
        Index(
            "uq_active_carrier", "carrier_code",
            unique=True, postgresql_where=text("carrier_code IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sn: Mapped[str] = mapped_column(unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(default="in_process")
    current_operation_seq: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=0)
    is_counted: Mapped[bool] = mapped_column(default=False, server_default="false")
    carrier_code: Mapped[str | None] = mapped_column(default=None)
```
顶部 import 行改为：
```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin
```

- [ ] **Step 2: 加 CarrierBinding 模型**

在 `production/models.py` 末尾加：
```python
class CarrierBinding(Base, TimestampMixin):
    __tablename__ = "carrier_binding"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    carrier_code: Mapped[str] = mapped_column()
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    unbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None)
```

- [ ] **Step 3: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "add carrier_code and carrier_binding"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移 add_column `serial_units.carrier_code` + create_index `uq_active_carrier`（带 postgresql_where）+ create_table `carrier_binding`（含两个 FK）。**打开迁移确认**：只加这一列+一索引+一表，不误删任何既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component/uq_operator_skill_user_skill）。若 autogenerate 把部分唯一索引写成普通 unique，手工改为带 `postgresql_where=sa.text("carrier_code IS NOT NULL")`。

- [ ] **Step 4: 写测试**

`tests/modules/production/test_carrier_models.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError
from lightmes.modules.production.models import SerialUnit, CarrierBinding


def _su(db_session, sn, carrier=None, status="pending"):
    su = SerialUnit(sn=sn, work_order_id=1, product_id=1,
                    status=status, carrier_code=carrier)
    db_session.add(su); db_session.flush(); return su


def test_serial_unit_carrier_defaults_none(db_session):
    su = _su(db_session, "SNX1")
    assert su.carrier_code is None


def test_active_carrier_unique(db_session):
    _su(db_session, "SNX2", carrier="PALLET-1")
    _su(db_session, "SNX3", carrier="PALLET-1")
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_carrier_null_not_conflicting(db_session):
    # 多个 carrier_code=None 不冲突（部分唯一索引仅约束非空）
    _su(db_session, "SNX4", carrier=None)
    _su(db_session, "SNX5", carrier=None)
    db_session.flush()  # 无异常即通过


def test_carrier_binding_row(db_session):
    su = _su(db_session, "SNX6", carrier="PALLET-9")
    b = CarrierBinding(serial_unit_id=su.id, carrier_code="PALLET-9")
    db_session.add(b); db_session.flush()
    assert b.id is not None and b.unbound_at is None and b.bound_at is not None
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_carrier_models.py -v` → PASS（4）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/production/models.py src/lightmes/migrations tests/modules/production/test_carrier_models.py
git commit -m "feat: add SerialUnit.carrier_code and carrier_binding table"
```

---

### Task 2: release_work_order 批量预生成 SerialUnit + WIP pending 过滤

**Files:**
- Modify: `src/lightmes/modules/production/service.py`（release_work_order 批量预生成 + 校验）
- Modify: `src/lightmes/modules/production/repository.py`（SerialUnitRepository 加计数/查询辅助）
- Test: `tests/modules/production/test_release_pregenerate.py`
- Test: `tests/modules/production/test_wip_pending_filter.py`

**Interfaces:**
- Consumes: `SnGenerator.next_sn(rule)`（已存在，行锁取号）；`SnRuleRepository.get`。
- Produces:
  - `release_work_order(work_order_id)` 下达时按 qty 建 N 条 SerialUnit(status="pending")
  - `SerialUnitRepository.count_pending_by_work_order(work_order_id) -> int`
  - `SerialUnitRepository.list_by_work_order` 已存在（wip 用，pending 由 WipService 过滤）

- [ ] **Step 1: repository 计数辅助**

在 `production/repository.py` `SerialUnitRepository` 内（`select` 已 import）加：
```python
    def count_pending_by_work_order(self, work_order_id: int) -> int:
        return self.db.execute(
            select(func.count()).select_from(SerialUnit).where(
                SerialUnit.work_order_id == work_order_id,
                SerialUnit.status == "pending")
        ).scalar_one()
```
文件顶部 import 补 `func`：`from sqlalchemy import select, func`。

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_release_pregenerate.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.repository import SerialUnitRepository


def _wo(db_session, qty=3, with_rule=True):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id)]))
    prod = ProductionService(db_session)
    rule_id = None
    if with_rule:
        rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
        rule_id = rule.id
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=qty, sn_rule_id=rule_id))
    return prod, wo


def test_release_pregenerates_pending_units(db_session):
    prod, wo = _wo(db_session, qty=3)
    prod.release_work_order(wo.id)
    repo = SerialUnitRepository(db_session)
    units = repo.list_by_work_order(wo.id)
    assert len(units) == 3
    assert all(u.status == "pending" and u.carrier_code is None
               and u.current_operation_seq == 0 for u in units)
    # SN 连续
    sns = sorted(u.sn for u in units)
    assert sns == ["SN00001", "SN00002", "SN00003"]
    assert repo.count_pending_by_work_order(wo.id) == 3


def test_release_requires_sn_rule(db_session):
    prod, wo = _wo(db_session, qty=3, with_rule=False)
    with pytest.raises(ValueError):
        prod.release_work_order(wo.id)


def test_release_requires_positive_qty(db_session):
    # qty=0 的工单下达应拒绝
    prod, wo = _wo(db_session, qty=0)
    with pytest.raises(ValueError):
        prod.release_work_order(wo.id)


def test_release_twice_no_duplicate(db_session):
    prod, wo = _wo(db_session, qty=2)
    prod.release_work_order(wo.id)
    with pytest.raises(ValueError):  # 已 released 不可再下达
        prod.release_work_order(wo.id)
    assert SerialUnitRepository(db_session).count_pending_by_work_order(wo.id) == 2
```

- [ ] **Step 3: 运行确认失败，改 release_work_order**

在 `production/service.py`：顶部 import 补：
```python
from lightmes.modules.production.models import SnRule, WorkOrder, SerialUnit
from lightmes.modules.production.repository import (
    SnRuleRepository, WorkOrderRepository, SerialUnitRepository,
)
from lightmes.modules.production.sn_generator import validate_pattern, SnGenerator
```
`__init__` 加 `self.serial_units = SerialUnitRepository(db)`、`self.sn_gen = SnGenerator(db)`。
替换 `release_work_order`：
```python
    def release_work_order(self, work_order_id: int) -> WorkOrder:
        wo = self.work_orders.get(work_order_id)
        if wo is None:
            raise ValueError(f"工单不存在: {work_order_id}")
        if wo.status != "created":
            raise ValueError(f"仅 created 状态可下达, 当前: {wo.status}")
        if wo.qty <= 0:
            raise ValueError(f"工单数量须大于 0: {wo.qty}")
        if wo.sn_rule_id is None:
            raise ValueError("工单未配置 SN 规则，无法预生成 SN")
        rule = self.sn_rules.get(wo.sn_rule_id)
        if rule is None:
            raise ValueError("SN 规则不存在")
        wo.status = "released"
        # 批量预生成 SerialUnit（pending）
        for _ in range(wo.qty):
            new_sn = self.sn_gen.next_sn(rule)
            self.serial_units.add(SerialUnit(
                sn=new_sn, work_order_id=wo.id, product_id=wo.product_id,
                status="pending", current_operation_seq=0))
        self.db.flush()
        return wo
```

- [ ] **Step 4: WIP pending 过滤回归测试**

`tests/modules/production/test_wip_pending_filter.py`:
```python
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.wip_service import WipService


def test_wip_excludes_pending(db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=3, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 全部 pending → WIP 为空（WipService 只显示 in_process）
    assert WipService(db_session).wip_by_work_order(wo.id) == []
```
（WipService 已只返回 in_process，本测试锁定 pending 不显示这一行为，防回归。）

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run 两个测试文件 → PASS（4 + 1）。全量回归 → 全绿。
> 注意：现有测试中若有依赖"release 后无 SerialUnit / 首件过站时才生成 SN"的用例，会因预生成而变化——预期 Task 3 调整 pass_operation 后这些用例改为兼容（本 Task 若回归有红，记录待 Task 3 修，或在本 Task 顺带更新受影响用例断言）。运行全量回归，把受影响用例列出。
```bash
git add src/lightmes/modules/production/service.py src/lightmes/modules/production/repository.py tests/modules/production/test_release_pregenerate.py tests/modules/production/test_wip_pending_filter.py
git commit -m "feat: pre-generate pending SerialUnits at work order release"
```

---

### Task 3: pass_operation 载体码定位 + pending→in_process + work_order_code 取 pending

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`
- Modify: `src/lightmes/modules/production/repository.py`（SerialUnitRepository 加 get_active_by_carrier / first_pending_by_work_order）
- Test: `tests/modules/production/test_pass_carrier.py`

**Interfaces:**
- Consumes: SerialUnit(carrier_code/status)；现有 pass_operation 全流程。
- Produces:
  - `SerialUnitRepository.get_active_by_carrier(carrier_code) -> SerialUnit | None`（status NOT IN finished/scrapped）
  - `SerialUnitRepository.first_pending_by_work_order(work_order_id) -> SerialUnit | None`（status=pending，id 升序第一个）
  - pass_operation：SN 未命中→按 carrier_code 命中活跃单元；work_order_code 首件→取第一个 pending（不新生成 SN）；pending 单元过站 status→in_process。

- [ ] **Step 1: repository 查询辅助**

在 `production/repository.py` `SerialUnitRepository` 加：
```python
    def get_active_by_carrier(self, carrier_code: str) -> SerialUnit | None:
        return self.db.execute(
            select(SerialUnit).where(
                SerialUnit.carrier_code == carrier_code,
                SerialUnit.status.notin_(("finished", "scrapped")))
        ).scalar_one_or_none()

    def first_pending_by_work_order(self, work_order_id: int) -> SerialUnit | None:
        return self.db.execute(
            select(SerialUnit).where(
                SerialUnit.work_order_id == work_order_id,
                SerialUnit.status == "pending")
            .order_by(SerialUnit.id).limit(1)
        ).scalar_one_or_none()
```

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_pass_carrier.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, n_ops=2, qty=3):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return prod, wo, ws


def test_work_order_first_item_takes_first_pending(db_session):
    prod, wo, ws = _setup(db_session)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(
        work_station_id=ws[0].id, work_order_code="WO"))
    # 取第一个 pending（SN00001），不新生成
    assert r.sn == "SN00001"
    su = SerialUnitRepository(db_session).get_by_sn("SN00001")
    assert su.status == "in_process" and su.current_operation_seq == 1


def test_carrier_code_locates_active_unit(db_session):
    prod, wo, ws = _setup(db_session)
    su_repo = SerialUnitRepository(db_session)
    # 手工给第一个 pending 绑载体码 + 投产首工序（模拟已投产）
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))
    su = su_repo.get_by_sn(r.sn)
    su.carrier_code = "PALLET-7"
    db_session.flush()
    # 后续站扫载体码过站 → 命中同一单元推进工序2
    r2 = svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, sn="PALLET-7"))
    assert r2.sn == su.sn and r2.is_finished is True


def test_work_order_first_item_exhausted_blocks(db_session):
    prod, wo, ws = _setup(db_session, n_ops=1, qty=1)
    svc = OperationPassService(db_session)
    svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))
    # 唯一 pending 已投产 → 再用工单号首件 → 无 pending → 拦截
    with pytest.raises(BusinessRuleError):
        svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))


def test_sn_scan_still_works(db_session):
    prod, wo, ws = _setup(db_session)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))
    r2 = svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, sn=r.sn))
    assert r2.is_finished is True
```

- [ ] **Step 3: 运行确认失败，改 pass_operation**

在 `operation_pass_service.py` 定位段（当前第 36-73 行的"1+3. 定位工单与 SN"整段）替换为下述逻辑。关键改动：SN 未命中先试 carrier_code；work_order_code 分支取第一个 pending 而非生成新 SN：
```python
        # 1+3. 定位单元：SN → 载体码 → 工单号(取第一个 pending)
        su = None
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                su = self.serial_units.get_active_by_carrier(data.sn)
            if su is None:
                raise NotFoundError(f"未找到 SN 或载体码: {data.sn}")
            if su.status in ("finished", "scrapped"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {su.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首件过站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = self.serial_units.first_pending_by_work_order(wo.id)
            if su is None:
                raise BusinessRuleError("工单 SN 已全部投产")
```
删除原"3(续). 首件生成 SN"整段（第 62-73 行的 `if su is None:` 生成 SN 块）——不再需要，pending 单元已存在。
在写工序记录前的适当位置（原第 100-116 行"6. 写工序记录 + 乐观锁"之后、或第 165-170"状态复位"段）加 pending 转 in_process。定位到现有"10. 工单/返工件状态复位"段，把它改为同时处理 pending：
```python
        # 10. 工单/返工件状态复位
        if wo.status == "released":
            wo.status = "in_process"
        if su.status in ("reworking", "pending"):
            su.status = "in_process"
        self.db.flush()
```
> 注意：乐观锁 UPDATE（原第 108-116 行）用 `su.version` 更新 current_operation_seq；pending 单元 version 初始 0，正常。绑料/参数段不变。

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/production/test_pass_carrier.py -v` → PASS（4）。
全量回归：**重点看 test_operation_pass.py / test_operation_pass_skill.py / test_operation_pass_concurrency.py / test_station_*.py**——因首件 SN 现在来自预生成第一条（仍是 pattern 的 SEQ=1，如 X0001/SN00001），`res.sn == "X0001"` 类断言应仍成立（预生成第一条 = 首件）。若个别用例因"扫码识别改动"报错（如原 test 期望 SN 不存在时抛 NotFoundError 的文案变化），据实更新断言文案（错误类型不变）。全量 → 全绿。
```bash
git add src/lightmes/modules/production/operation_pass_service.py src/lightmes/modules/production/repository.py tests/modules/production/test_pass_carrier.py
git commit -m "feat: locate serial unit by carrier code and consume pending at first pass"
```

---

### Task 4: CarrierService（绑定投产 + 解绑）+ 首站可选工单查询

**Files:**
- Modify: `src/lightmes/modules/production/repository.py`（CarrierBindingRepository；WorkOrderRepository.selectable_for_station）
- Modify: `src/lightmes/modules/production/schemas.py`（CarrierBindInput/CarrierUnbindInput）
- Create: `src/lightmes/modules/production/carrier_service.py`
- Test: `tests/modules/production/test_carrier_service.py`

**Interfaces:**
- Consumes: `SerialUnitRepository.first_pending_by_work_order/get_active_by_carrier/get_by_sn`（Task 2/3）；`OperationPassService.pass_operation`；CarrierBinding 模型。
- Produces:
  - `CarrierBindingRepository`: `add(b) -> CarrierBinding`; `active_by_serial_unit(su_id) -> CarrierBinding | None`（unbound_at IS NULL 最新）
  - `WorkOrderRepository.selectable_for_station(line_id) -> list[WorkOrder]`（status in released/in_process 且 line_id 匹配）
  - `schemas.CarrierBindInput`(work_order_id:int, carrier_code:str, work_station_id:int, components:list[ComponentInput]=[], params:list[ParamInput]=[]) ；`CarrierUnbindInput`(scan:str)
  - `CarrierService(db)`:
    - `bind_and_pass_first(work_order_id, carrier_code, work_station_id, operator_id, components=[], params=[]) -> OperationPassResult`
    - `unbind(scan, operator_id) -> SerialUnit`

- [ ] **Step 1: repository + schemas**

在 `production/repository.py` `WorkOrderRepository` 加：
```python
    def selectable_for_station(self, line_id: int) -> list[WorkOrder]:
        return list(self.db.execute(
            select(WorkOrder).where(
                WorkOrder.line_id == line_id,
                WorkOrder.status.in_(("released", "in_process")))
            .order_by(WorkOrder.id)
        ).scalars().all())
```
文件末尾加：
```python
class CarrierBindingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, b: "CarrierBinding") -> "CarrierBinding":
        self.db.add(b); self.db.flush(); return b

    def active_by_serial_unit(self, serial_unit_id: int) -> "CarrierBinding | None":
        return self.db.execute(
            select(CarrierBinding).where(
                CarrierBinding.serial_unit_id == serial_unit_id,
                CarrierBinding.unbound_at.is_(None))
            .order_by(CarrierBinding.id.desc()).limit(1)
        ).scalar_one_or_none()
```
文件顶部 import 补 `CarrierBinding`：`from lightmes.modules.production.models import (OperationParam, OperationRecord, SerialUnit, SnRule, WorkOrder, CarrierBinding,)`。
在 `production/schemas.py` 末尾加（`ComponentInput`/`ParamInput` 已在本文件）：
```python
class CarrierBindInput(BaseModel):
    work_order_id: int
    carrier_code: str
    work_station_id: int
    components: list[ComponentInput] = []
    params: list[ParamInput] = []


class CarrierUnbindInput(BaseModel):
    scan: str
```

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_carrier_service.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.production.repository import (
    SerialUnitRepository, CarrierBindingRepository, WorkOrderRepository,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError, NotFoundError


def _setup(db_session, qty=3, n_ops=2):
    md = MasterDataService(db_session)
    user = User(username="cop", password_hash="x", display_name="工")
    db_session.add(user); db_session.flush()
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return prod, wo, ws, user, line


def test_bind_and_pass_assigns_first_pending_in_order(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    r1 = svc.bind_and_pass_first(wo.id, "PAL-1", ws[0].id, user.id)
    r2 = svc.bind_and_pass_first(wo.id, "PAL-2", ws[0].id, user.id)
    assert r1.sn == "SN00001" and r2.sn == "SN00002"  # 顺序赋值
    su1 = SerialUnitRepository(db_session).get_by_sn("SN00001")
    assert su1.carrier_code == "PAL-1" and su1.status == "in_process"
    assert CarrierBindingRepository(db_session).active_by_serial_unit(su1.id) is not None


def test_bind_exhausted_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-1", ws[0].id, user.id)
    with pytest.raises(BusinessRuleError):
        svc.bind_and_pass_first(wo.id, "PAL-2", ws[0].id, user.id)


def test_bind_duplicate_carrier_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-DUP", ws[0].id, user.id)
    with pytest.raises(BusinessRuleError):  # 载体码已绑活跃单元
        svc.bind_and_pass_first(wo.id, "PAL-DUP", ws[0].id, user.id)


def test_unbind_clears_and_allows_reuse(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-R", ws[0].id, user.id)
    su = svc.unbind("PAL-R", user.id)
    assert su.carrier_code is None
    binding = CarrierBindingRepository(db_session).active_by_serial_unit(su.id)
    assert binding is None  # 已无活跃绑定
    # 载体码可复用：绑到下一个 pending
    r2 = svc.bind_and_pass_first(wo.id, "PAL-R", ws[0].id, user.id)
    assert r2.sn == "SN00002"


def test_unbind_by_sn(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    r = svc.bind_and_pass_first(wo.id, "PAL-X", ws[0].id, user.id)
    su = svc.unbind(r.sn, user.id)  # 用 SN 解绑
    assert su.carrier_code is None


def test_unbind_unknown_raises(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    with pytest.raises(NotFoundError):
        svc.unbind("NOPE", user.id)


def test_selectable_for_station_filters(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    # wo 已 released → 可选
    sel = WorkOrderRepository(db_session).selectable_for_station(line.id)
    assert wo.id in [w.id for w in sel]
    # 异产线不含
    from lightmes.modules.masterdata.service import MasterDataService as MD
    other = MD(db_session).create_line(LineCreate(code="OTH", name="别线"))
    db_session.flush()
    assert WorkOrderRepository(db_session).selectable_for_station(other.id) == []
```

- [ ] **Step 3: 运行确认失败，写 CarrierService**

`src/lightmes/modules/production/carrier_service.py`:
```python
from sqlalchemy.orm import Session

from lightmes.modules.production.models import CarrierBinding, SerialUnit
from lightmes.modules.production.repository import (
    SerialUnitRepository, WorkOrderRepository, CarrierBindingRepository,
)
from lightmes.modules.production.schemas import OperationPassInput, OperationPassResult
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.shared.errors import BusinessRuleError, NotFoundError


class CarrierService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.work_orders = WorkOrderRepository(db)
        self.bindings = CarrierBindingRepository(db)

    def bind_and_pass_first(
        self, work_order_id: int, carrier_code: str, work_station_id: int,
        operator_id: int | None, components=None, params=None,
    ) -> OperationPassResult:
        su = self.serial_units.first_pending_by_work_order(work_order_id)
        if su is None:
            raise BusinessRuleError("工单 SN 已全部投产，请选择新工单")
        if self.serial_units.get_active_by_carrier(carrier_code) is not None:
            raise BusinessRuleError(f"载体码已绑定其他产品，请先解绑: {carrier_code}")
        su.carrier_code = carrier_code
        self.bindings.add(CarrierBinding(
            serial_unit_id=su.id, carrier_code=carrier_code, operator_id=operator_id))
        # 过首工序（pass_operation 内 pending→in_process）
        return OperationPassService(self.db).pass_operation(OperationPassInput(
            work_station_id=work_station_id, sn=su.sn, operator_id=operator_id,
            components=components or [], params=params or []))

    def unbind(self, scan: str, operator_id: int | None) -> SerialUnit:
        # 权限校验钩子（P2e 预留；后续角色管理模块在此接入）：
        # 目前任何登录用户可解绑，暂不做角色判断。
        su = self.serial_units.get_by_sn(scan)
        if su is None:
            su = self.serial_units.get_active_by_carrier(scan)
        if su is None:
            raise NotFoundError(f"未找到 SN 或载体码: {scan}")
        binding = self.bindings.active_by_serial_unit(su.id)
        if binding is not None:
            from datetime import datetime
            binding.unbound_at = datetime.now()
        su.carrier_code = None
        self.db.flush()
        return su
```
> `bind_and_pass_first` 里 `su.carrier_code=carrier_code` 与 pass_operation 在同一事务；若 pass_operation 抛异常（如防跳站/技能不足），get_db 请求层会 rollback，绑定不落库（页面处理器也会 rollback，见 Task 5）。

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run → PASS（7）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/production/carrier_service.py src/lightmes/modules/production/repository.py src/lightmes/modules/production/schemas.py tests/modules/production/test_carrier_service.py
git commit -m "feat: add CarrierService (bind-and-pass-first, unbind) + selectable work orders"
```

---

### Task 5: 工位作业首站流 + 解绑页 + 首页导航调整

**Files:**
- Modify: `src/lightmes/modules/production/router.py`（select-wo / bind-and-pass 路由）
- Modify: `src/lightmes/templates/production/station.html`（先选工单流）
- Create: `src/lightmes/templates/production/partials/station_wo_selected.html`（选中工单+扫载体码）
- Create: `src/lightmes/templates/production/partials/station_bind_result.html`（投产结果/用完提示）
- Modify: `src/lightmes/modules/trace/router.py`（carrier-unbind 页面）
- Create: `src/lightmes/templates/trace/carrier_unbind.html`
- Modify: `src/lightmes/templates/home.html`（移除 scan 卡片；加解绑入口）
- Test: `tests/modules/production/test_station_carrier_pages.py`
- Test: `tests/modules/trace/test_carrier_unbind_page.py`

**Interfaces:**
- Consumes: `CarrierService.bind_and_pass_first/unbind`（Task 4）；`WorkOrderRepository.selectable_for_station`；`SerialUnitRepository.count_pending_by_work_order`；`MasterDataQueryService.get_work_station`（取作业站 line_id）；`current_user_or_none`。
- Produces:
  - `GET /production/station/select-wo`（Form work_station_id, scan=工单号）→ 校验 + 渲染 station_wo_selected（工单信息 + 剩余 pending 数）
  - `POST /production/station/bind-and-pass`（Form work_station_id, work_order_id, carrier_code, 组件/参数）→ CarrierService.bind_and_pass_first → station_bind_result（成功 + 剩余数 / 用完提示）
  - `GET /trace/carrier-unbind` + `POST /trace/carrier-unbind`（Form scan）→ CarrierService.unbind

- [ ] **Step 1: 写失败测试（页面）**

`tests/modules/production/test_station_carrier_pages.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="sc", password="pw12345", display_name="Sc"))
    db_session.flush()
    client.post("/login", data={"username": "sc", "password": "pw12345"})


def _released_wo(db_session, qty=2, status_release=True):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    if status_release:
        prod.release_work_order(wo.id)
    db_session.flush()
    return ws, wo


def test_select_wo_shows_remaining(client, db_session):
    ws, wo = _released_wo(db_session, qty=2)
    _login(client, db_session)
    resp = client.post("/production/station/select-wo",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200 and ("剩余" in resp.text or "2" in resp.text)


def test_select_wo_created_rejected(client, db_session):
    ws, wo = _released_wo(db_session, qty=2, status_release=False)  # created 未下达
    _login(client, db_session)
    resp = client.post("/production/station/select-wo",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200 and "✗" in resp.text


def test_bind_and_pass_produces_and_shows_remaining(client, db_session):
    ws, wo = _released_wo(db_session, qty=2)
    _login(client, db_session)
    resp = client.post("/production/station/bind-and-pass",
                       data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-1"})
    assert resp.status_code == 200 and ("已投产" in resp.text or "SN00001" in resp.text)


def test_bind_requires_login(client, db_session):
    ws, wo = _released_wo(db_session, qty=2)
    resp = client.post("/production/station/bind-and-pass",
                       data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-1"})
    assert resp.status_code == 401


def test_bind_exhausted_prompts_new_wo(client, db_session):
    ws, wo = _released_wo(db_session, qty=1)
    _login(client, db_session)
    client.post("/production/station/bind-and-pass",
                data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-1"})
    resp = client.post("/production/station/bind-and-pass",
                       data={"work_station_id": str(ws.id), "work_order_id": str(wo.id), "carrier_code": "PAL-2"})
    assert resp.status_code == 200 and ("✗" in resp.text or "全部投产" in resp.text)
```

`tests/modules/trace/test_carrier_unbind_page.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.auth.repository import UserRepository


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="ub", password="pw12345", display_name="Ub"))
    db_session.flush()
    client.post("/login", data={"username": "ub", "password": "pw12345"})


def test_unbind_page_renders(client, db_session):
    resp = client.get("/trace/carrier-unbind")
    assert resp.status_code == 200 and "解绑" in resp.text


def test_unbind_submit(client, db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=2, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    _login(client, db_session)
    uid = UserRepository(db_session).get_by_username("ub").id
    CarrierService(db_session).bind_and_pass_first(wo.id, "PAL-U", ws.id, uid)
    db_session.flush()
    resp = client.post("/trace/carrier-unbind", data={"scan": "PAL-U"})
    assert resp.status_code == 200 and "✓" in resp.text


def test_unbind_requires_login(client, db_session):
    resp = client.post("/trace/carrier-unbind", data={"scan": "X"})
    assert resp.status_code == 401
```

- [ ] **Step 2: 运行确认失败，写路由 + 模板**

在 `production/router.py`：import 补 `from lightmes.modules.production.carrier_service import CarrierService`、`from lightmes.modules.production.repository import SerialUnitRepository`、`from lightmes.modules.masterdata.query_service import MasterDataQueryService`、`ComponentInput, ParamInput`（如未 import）。加两个路由：
```python
@router.post("/production/station/select-wo", response_class=HTMLResponse)
def station_select_wo(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    ws = MasterDataQueryService(db).get_work_station(work_station_id)
    if ws is None:
        return templates.TemplateResponse(
            request, "production/partials/station_bind_result.html",
            {"error": f"作业站不存在: {work_station_id}", "work_station_id": work_station_id})
    wo = ProductionService(db).work_orders.get_by_code(scan)
    if wo is None or wo.status not in ("released", "in_process") or wo.line_id != ws.line_id:
        return templates.TemplateResponse(
            request, "production/partials/station_bind_result.html",
            {"error": "工单不可投产（需已下达且属本产线）", "work_station_id": work_station_id})
    remaining = SerialUnitRepository(db).count_pending_by_work_order(wo.id)
    return templates.TemplateResponse(
        request, "production/partials/station_wo_selected.html",
        {"wo": wo, "remaining": remaining, "work_station_id": work_station_id})


@router.post("/production/station/bind-and-pass", response_class=HTMLResponse)
def station_bind_and_pass(
    request: Request,
    work_station_id: int = Form(...),
    work_order_id: int = Form(...),
    carrier_code: str = Form(...),
    component_product_id: list[int] = Form(default=[]),
    component_batch: list[str] = Form(default=[]),
    param_key: list[str] = Form(default=[]),
    param_value: list[str] = Form(default=[]),
    param_unit: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    components = [
        ComponentInput(component_product_id=pid, component_batch_no=b.strip())
        for pid, b in zip(component_product_id, component_batch) if b.strip()]
    params = []
    for i, key in enumerate(param_key):
        if not key.strip():
            continue
        val = param_value[i] if i < len(param_value) else ""
        if not val.strip():
            continue
        unit = param_unit[i].strip() if i < len(param_unit) and param_unit[i].strip() else None
        params.append(ParamInput(param_key=key.strip(), param_value=val.strip(), unit=unit))
    try:
        result = CarrierService(db).bind_and_pass_first(
            work_order_id, carrier_code.strip(), work_station_id, user.id,
            components=components, params=params)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_bind_result.html",
            {"error": e.detail, "work_station_id": work_station_id})
    remaining = SerialUnitRepository(db).count_pending_by_work_order(work_order_id)
    return templates.TemplateResponse(
        request, "production/partials/station_bind_result.html",
        {"result": result, "remaining": remaining, "work_order_id": work_order_id,
         "work_station_id": work_station_id})
```
改 `station.html` 为"先选工单"流：
```html
{% extends "base.html" %}
{% block title %}工位作业{% endblock %}
{% block content %}
<h1 class="page-title">工位作业主界面 <small>作业站 #{{ work_station_id }}</small></h1>
<div class="card">
  <div class="card__title">① 选择投产工单（首站）</div>
  <form class="form-row" hx-post="/production/station/select-wo" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=scan]').value=''">
    <div class="field"><label>作业站</label><input name="work_station_id" value="{{ work_station_id }}"></div>
    <div class="field" style="flex:1"><label>扫/输入工单号</label>
      <input name="scan" placeholder="工单号" autofocus></div>
    <button type="submit">选择工单</button>
  </form>
  <div class="nav-card__desc">后续工序请用下方"按 SN/载体码加载"。</div>
  <form class="form-row" hx-post="/production/station/load" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=scan]').value=''">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <div class="field" style="flex:1"><label>② 后续站：扫 SN / 载体码</label>
      <input name="scan" placeholder="SN 或载体码"></div>
    <button type="submit">加载</button>
  </form>
</div>
<div id="station-root" class="station-root"></div>
{% endblock %}
```
新建 `partials/station_wo_selected.html`（选中工单 → 连续扫载体码投产）:
```html
<div class="card">
  <div class="card__title">投产工单 {{ wo.code }} <span class="badge">剩余待投产 {{ remaining }}</span></div>
  <form class="form-row" hx-post="/production/station/bind-and-pass" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=carrier_code]').value=''">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <input type="hidden" name="work_order_id" value="{{ wo.id }}">
    <div class="field" style="flex:1"><label>扫载体码（托盘/来料唯一码）</label>
      <input name="carrier_code" placeholder="扫载体码投产下一件" required autofocus></div>
    <button type="submit">投产过站</button>
  </form>
</div>
```
新建 `partials/station_bind_result.html`（结果/用完/错误）:
```html
{% if error %}
<div class="alert alert--danger">✗ {{ error }}</div>
<div class="card"><div class="nav-card__desc">请重新 <a href="/production/station?work_station_id={{ work_station_id }}">选择工单</a>。</div></div>
{% else %}
<div class="alert alert--ok">✓ 已投产 <strong>{{ result.sn }}</strong> — 过 工序{{ result.passed_op.seq }} {{ result.passed_op.name }}
  {% if result.next_op %} → 下一站：工序{{ result.next_op.seq }} {{ result.next_op.name }}{% endif %}</div>
{% if remaining and remaining > 0 %}
<div class="card">
  <div class="card__title">继续投产 <span class="badge">剩余 {{ remaining }}</span></div>
  <form class="form-row" hx-post="/production/station/bind-and-pass" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=carrier_code]').value=''">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <input type="hidden" name="work_order_id" value="{{ work_order_id }}">
    <div class="field" style="flex:1"><label>扫下一载体码</label>
      <input name="carrier_code" placeholder="扫载体码" autofocus></div>
    <button type="submit">投产过站</button>
  </form>
</div>
{% else %}
<div class="alert alert--ok">✓ 工单已全部投产，请 <a href="/production/station?work_station_id={{ work_station_id }}">选择新工单</a>。</div>
{% endif %}
{% endif %}
```
在 `trace/router.py`：import 补 `from lightmes.modules.production.carrier_service import CarrierService`。加：
```python
@router.get("/trace/carrier-unbind", response_class=HTMLResponse)
def carrier_unbind_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/carrier_unbind.html")


@router.post("/trace/carrier-unbind", response_class=HTMLResponse)
def carrier_unbind_submit(
    request: Request, scan: str = Form(...), db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        su = CarrierService(db).unbind(scan, user.id)
    except DomainError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(e.detail)}</div>')
    return HTMLResponse(
        f'<div style="color:green">✓ {escape(su.sn)} 已解绑载体码</div>')
```
新建 `templates/trace/carrier_unbind.html`:
```html
{% extends "base.html" %}
{% block title %}载体码解绑{% endblock %}
{% block content %}
<h1 class="page-title">载体码解绑</h1>
<div class="card">
  <div class="card__title">解绑（扫 SN 或载体码）</div>
  <form class="form-row" hx-post="/trace/carrier-unbind" hx-target="#result" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=scan]').value=''">
    <div class="field" style="flex:1"><label>SN / 载体码</label>
      <input name="scan" placeholder="扫 SN 或载体码" required autofocus></div>
    <button type="submit">解绑</button>
  </form>
  <div id="result" class="result-slot"></div>
</div>
{% endblock %}
```
改 `home.html`：删除 `/production/scan` 那张 nav-card（扫码过站，第 54-58 行）；在追溯管理卡片区加解绑入口：
```html
    <a class="nav-card" href="/trace/carrier-unbind">
      <span class="nav-card__icon">🔗</span>
      <div class="nav-card__name">载体码解绑</div>
      <div class="nav-card__desc">解除 SN 与载体码绑定</div>
    </a>
```

- [ ] **Step 3: 运行测试 + 回归 + Commit**

Run 两个页面测试文件 → PASS。全量回归：注意 `test_scan_pages.py` 仍应通过（/production/scan 路由未删）；home.html 改动若有断言"扫码过站"字样的测试需更新。全量 → 全绿。
```bash
git add src/lightmes/modules/production/router.py src/lightmes/templates/production src/lightmes/modules/trace/router.py src/lightmes/templates/trace/carrier_unbind.html src/lightmes/templates/home.html tests/modules/production/test_station_carrier_pages.py tests/modules/trace/test_carrier_unbind_page.py
git commit -m "feat: first-station carrier bind-and-pass flow + carrier unbind page"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2e spec §3/§4/§5/§7/§8）：
- SerialUnit.carrier_code + uq_active_carrier + carrier_binding 表 + 迁移 → Task 1 ✅
- release 批量预生成 pending + qty/sn_rule 校验 + WIP pending 过滤 → Task 2 ✅
- pass_operation 载体码定位 + pending→in_process + work_order_code 取 pending（不超 qty）→ Task 3 ✅
- CarrierService（bind_and_pass_first 顺序取号/用完拦/重复绑拦；unbind + 历史 + 角色钩子占位）+ selectable_for_station → Task 4 ✅
- 首站流（选工单校验 released|in_process+本产线 → 扫载体码投产 → 剩余数/用完提示）+ 解绑页 + 首页移除 scan 入口 → Task 5 ✅
- 自动识别（先 SN 后载体码）→ Task 3（pass）+ Task 4（unbind）✅
- operator_id 服务端赋值 / require_login / 领域异常 rollback → Task 5 ✅

**占位符扫描**：所有 code step 含完整代码。Task 3 对 operation_pass_service.py 的改动以"替换定位段/删除生成段/改状态复位段"精确描述并给出完整新代码块。

**类型一致性**：`SerialUnit.carrier_code`、`CarrierBinding`、`SerialUnitRepository.{count_pending_by_work_order/get_active_by_carrier/first_pending_by_work_order}`、`WorkOrderRepository.selectable_for_station`、`CarrierBindingRepository.{add/active_by_serial_unit}`、`CarrierBindInput/CarrierUnbindInput`、`CarrierService.{bind_and_pass_first/unbind}` —— 定义处（Task 1/2/3/4）与引用处（Task 4/5）一致 ✅。

**关键回归风险**（已在 Task 3 Step 4 标注）：现有 `test_operation_pass*.py` / `test_station_*.py` 依赖"首件过站生成 SN X0001"。预生成后第一条 pending 即 X0001，work_order_code 分支改取第一个 pending → `res.sn=="X0001"` 仍成立，绝大多数用例不受影响。个别断言错误文案（如 NotFound 消息）若变化据实更新，错误类型不变。全量回归为每个 Task 的 gate。

**迁移**：Task 1 加一列 + 一部分唯一索引 + 一表；打开迁移核对不误删既有索引，确认部分唯一索引带 postgresql_where。

