# 工艺路线编辑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `masterdata/routings` 加编辑能力——详情页可改路线头（name/active）、增删改工序（含 allowed 重写）、物理删路线；被工单引用时拒改/拒删。

**Architecture:** `MasterDataService` 加 6 个写方法（每个含工单引用校验 + 对应字段校验）；新 7 个路由（GET 详情 + 6 POST 写）；新详情页模板（路线头卡片 + 工序表格每行 form + allowed 复选框组 + 危险按钮）；列表 code 列加详情链接。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0 风格；repository 只 flush；事务边界 get_db（请求层 commit/rollback）；无新表/无迁移。
- **工单引用校验**：每个写方法（update_routing_head/set_routing_status/update_operation/add_operation/delete_operation/delete_routing）内部第一步调 `WorkOrderRepository.count_by_routing(routing_id) > 0 → ValueError(f"该路线已被 N 个工单引用，请先处理工单")`。
- **active 冲突校验**：`set_routing_status('active')` 时若 `get_active_by_product(product_id)` 返回非自己 → ValueError("该产品已有 active 路线 #{other_id}，请先设为 inactive")。切 inactive 总是允许。
- **default ∈ allowed 校验**：update_operation/add_operation 时校验 default_work_station_id ∈ allowed_work_station_ids；allowed 非空；每个 ws 存在；重复 ws_id 拒（沿用 P2g service 已有"重复"校验思路）。
- **seq 冲突校验**：update/add operation 时 seq 不与同 routing 其他工序冲突（uq_operation_routing_seq）。
- **物理删除级联**：`delete_routing` 先删该 routing 下所有 operations（operation_work_stations 跟随 CASCADE 自动清），再删 routing（operations.routing_id FK 是 NO ACTION，必须先删 operations）。
- **active 切换独立按钮 + 独立端点** `/masterdata/routings/{id}/status`；**删除路线后 302 回 `/masterdata/routings`**；**工序行编辑提交后 HTMX 局部刷新该行**。
- operator_id 服务端赋值（不需要——这些是主数据路由，但 require_login 守卫一致）；写操作 require_login（未登录 401+HX-Redirect /login）；NO CDN；Jinja2 `{{ }}` 自动转义；删除按钮 `onclick="return confirm(...)"` 防 misclick。
- 提交前缀 `feat:`/`test:`/`chore:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

```
src/lightmes/modules/masterdata/
├── service.py           # 改：加 update_routing_head/set_routing_status/update_operation/add_operation/delete_operation/delete_routing + 工单引用校验
├── repository.py        # 改：RoutingRepository.delete；OperationRepository.delete/list_by_routing；OperationWorkStationRepository.delete_by_operation
src/lightmes/modules/production/
└── repository.py        # 改：WorkOrderRepository.count_by_routing
src/lightmes/modules/masterdata/router.py  # 改：7 个新路由（GET 详情 + 6 POST 写）
src/lightmes/templates/masterdata/
├── routings.html        # 改：列表 code 列加 <a href>
└── routing_detail.html  # 新：详情页
tests/modules/masterdata/test_routing_edit.py  # 新：服务层 + 端到端
tests/modules/production/test_work_order_repo.py  # 改（若存在）或新建：count_by_routing
```

---

### Task 1: 服务层 + 工单引用校验 + Repository 扩展

**Files:**
- Modify: `src/lightmes/modules/production/repository.py`（WorkOrderRepository.count_by_routing）
- Modify: `src/lightmes/modules/masterdata/repository.py`（RoutingRepository.delete；OperationWorkStationRepository.delete_by_operation）
- Modify: `src/lightmes/modules/masterdata/service.py`（6 个新方法）
- Test: `tests/modules/masterdata/test_routing_edit.py`

**Interfaces:**
- Consumes: `WorkOrderRepository`（既有）、`RoutingRepository`（既有 + delete）、`OperationWorkStationRepository.add/list_by_operation`（既有 + delete_by_operation）、`MasterDataService.create_routing`（既有，含 default∈allowed + 重复 + seq 重复 + 技能等级校验逻辑可复用）。
- Produces:
  - `WorkOrderRepository.count_by_routing(routing_id: int) -> int`
  - `RoutingRepository.delete(routing_id: int) -> None`
  - `OperationWorkStationRepository.delete_by_operation(op_id: int) -> None`
  - `MasterDataService.update_routing_head(routing_id, name) -> Routing`
  - `MasterDataService.set_routing_status(routing_id, status) -> Routing`
  - `MasterDataService.update_operation(operation_id, *, seq, code, name, default_work_station_id, allowed_work_station_ids, required_skill_id, required_level, is_mandatory) -> Operation`
  - `MasterDataService.add_operation(routing_id, *, seq, code, name, default_work_station_id, allowed_work_station_ids, required_skill_id, required_level, is_mandatory) -> Operation`
  - `MasterDataService.delete_operation(operation_id) -> None`
  - `MasterDataService.delete_routing(routing_id) -> None`

- [ ] **Step 1: WorkOrderRepository.count_by_routing**

在 `src/lightmes/modules/production/repository.py` `WorkOrderRepository` 内（约第 34-48 行）加：
```python
    def count_by_routing(self, routing_id: int) -> int:
        from sqlalchemy import func
        return self.db.execute(
            select(func.count()).select_from(WorkOrder)
            .where(WorkOrder.routing_id == routing_id)
        ).scalar_one()
```
（`select` 已 import；`func` 顶部 import 一句加。）

- [ ] **Step 2: RoutingRepository.delete + OperationWorkStationRepository.delete_by_operation**

在 `masterdata/repository.py`：
- `RoutingRepository` 加：
```python
    def delete(self, routing_id: int) -> None:
        r = self.get(routing_id)
        if r is not None:
            self.db.delete(r); self.db.flush()
```
- `OperationWorkStationRepository` 加：
```python
    def delete_by_operation(self, op_id: int) -> None:
        self.db.execute(
            sqlalchemy_delete(OperationWorkStation)
            .where(OperationWorkStation.operation_id == op_id)
        )
        self.db.flush()
```
顶部 import 加 `from sqlalchemy import delete as sqlalchemy_delete`（避免和 Python 关键字冲突；或用 `sqlalchemy.delete`）。

- [ ] **Step 3: 写失败测试**

`tests/modules/masterdata/test_routing_edit.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.repository import (
    OperationWorkStationRepository, RoutingRepository,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.repository import WorkOrderRepository


def _full_setup(db_session, with_work_order=False):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
        OperationCreate(seq=20, code="OP20", name="工序20",
                        default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
    ]))
    if with_work_order:
        prod = ProductionService(db_session)
        rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
        prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id,
            line_id=line.id, qty=5, sn_rule_id=rule.id))
    db_session.flush()
    return md, p, line, (ws1, ws2), routing


def test_update_routing_head(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    updated = md.update_routing_head(routing.id, "新名称")
    assert updated.name == "新名称"


def test_update_routing_head_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    with pytest.raises(ValueError, match="工单"):
        md.update_routing_head(routing.id, "新名称")


def test_set_routing_status_active_conflict(db_session):
    md, p, line, wss, routing = _full_setup(db_session)  # routing active
    other = md.create_routing(RoutingCreate(code="RT2", name="路线2", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OPX", name="工序X",
                        default_work_station_id=wss[0].id, allowed_work_station_ids=[wss[0].id])]))
    # other 默认 inactive（同产品已有 active routing）
    assert other.status == "inactive"
    with pytest.raises(ValueError, match="active 路线"):
        md.set_routing_status(other.id, "active")  # 冲突
    # 先把 routing inactive，再 active other → 通过
    md.set_routing_status(routing.id, "inactive")
    md.set_routing_status(other.id, "active")
    db_session.refresh(other)
    assert other.status == "active"


def test_set_routing_status_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    with pytest.raises(ValueError, match="工单"):
        md.set_routing_status(routing.id, "inactive")


def test_update_operation_changes_fields_and_allowed(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    ops = md.routings.operations_of(routing.id)
    op0 = ops[0]
    updated = md.update_operation(
        op0.id, seq=15, code="OP15", name="新工序名",
        default_work_station_id=wss[1].id,
        allowed_work_station_ids=[wss[0].id, wss[1].id],
        required_skill_id=None, required_level=None, is_mandatory=True)
    assert updated.seq == 15 and updated.name == "新工序名"
    allowed = OperationWorkStationRepository(db_session).list_by_operation(updated.id)
    assert {a.work_station_id for a in allowed} == {wss[0].id, wss[1].id}


def test_update_operation_rejects_default_not_in_allowed(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    op0 = md.routings.operations_of(routing.id)[0]
    with pytest.raises(ValueError, match="默认作业站"):
        md.update_operation(
            op0.id, seq=10, code="OP10", name="工序10",
            default_work_station_id=wss[0].id,
            allowed_work_station_ids=[wss[1].id],  # default wss[0] 不在
            required_skill_id=None, required_level=None, is_mandatory=True)


def test_update_operation_rejects_seq_conflict(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    ops = md.routings.operations_of(routing.id)
    op0 = ops[0]  # seq=10
    with pytest.raises(ValueError, match="seq"):  # 改成 op1 的 seq=20
        md.update_operation(
            op0.id, seq=20, code="OP10", name="工序10",
            default_work_station_id=wss[0].id, allowed_work_station_ids=[wss[0].id],
            required_skill_id=None, required_level=None, is_mandatory=True)


def test_update_operation_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    op0 = md.routings.operations_of(routing.id)[0]
    with pytest.raises(ValueError, match="工单"):
        md.update_operation(
            op0.id, seq=15, code="OP15", name="x",
            default_work_station_id=wss[0].id, allowed_work_station_ids=[wss[0].id],
            required_skill_id=None, required_level=None, is_mandatory=True)


def test_add_operation(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    new_op = md.add_operation(
        routing.id, seq=30, code="OP30", name="工序30",
        default_work_station_id=wss[1].id, allowed_work_station_ids=[wss[1].id],
        required_skill_id=None, required_level=None, is_mandatory=True)
    assert new_op.id is not None and new_op.seq == 30


def test_delete_operation(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    op0 = md.routings.operations_of(routing.id)[0]
    md.delete_operation(op0.id)
    db_session.flush()
    remaining = md.routings.operations_of(routing.id)
    assert len(remaining) == 1 and remaining[0].seq == 20
    # 关联表跟随清
    assert OperationWorkStationRepository(db_session).list_by_operation(op0.id) == []


def test_delete_operation_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    op0 = md.routings.operations_of(routing.id)[0]
    with pytest.raises(ValueError, match="工单"):
        md.delete_operation(op0.id)


def test_delete_routing_cascades(db_session):
    md, p, line, wss, routing = _full_setup(db_session)
    op_ids = [o.id for o in md.routings.operations_of(routing.id)]
    md.delete_routing(routing.id)
    db_session.flush()
    assert RoutingRepository(db_session).get(routing.id) is None
    # operations 全部级联清
    for oid in op_ids:
        assert OperationWorkStationRepository(db_session).list_by_operation(oid) == []


def test_delete_routing_rejected_when_work_order_referenced(db_session):
    md, p, line, wss, routing = _full_setup(db_session, with_work_order=True)
    with pytest.raises(ValueError, match="工单"):
        md.delete_routing(routing.id)
```

- [ ] **Step 4: 运行确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_routing_edit.py -v`
Expected: FAIL（方法不存在）。

- [ ] **Step 5: 写服务层 6 个方法**

在 `masterdata/service.py` `MasterDataService` 内（建议加在 create_routing 之后），先加 `__init__` 注入：
```python
        self.wo_repo = WorkOrderRepository(db)  # 工单引用校验用
```
（顶部 import 加 `from lightmes.modules.production.repository import WorkOrderRepository`。）

加 6 个方法：
```python
    def _check_no_work_order(self, routing_id: int) -> None:
        n = self.wo_repo.count_by_routing(routing_id)
        if n > 0:
            raise ValueError(f"该路线已被 {n} 个工单引用，请先处理工单")

    def update_routing_head(self, routing_id: int, name: str) -> Routing:
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        if not name.strip():
            raise ValueError("路线名称不能为空")
        self._check_no_work_order(routing_id)
        routing.name = name.strip()
        self.db.flush()
        return routing

    def set_routing_status(self, routing_id: int, status: str) -> Routing:
        if status not in ("active", "inactive"):
            raise ValueError(f"无效状态: {status}")
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        self._check_no_work_order(routing_id)
        if status == "active":
            other = self.routings.get_active_by_product(routing.product_id)
            if other is not None and other.id != routing_id:
                raise ValueError(
                    f"该产品已有 active 路线 #{other.id}（{other.code}），请先设为 inactive")
        routing.status = status
        self.db.flush()
        return routing

    def _validate_op_fields(self, routing_id, seq, code, name,
                            default_work_station_id, allowed_work_station_ids,
                            required_skill_id, required_level, exclude_op_id=None):
        # default 存在 + allowed 非空 + default ∈ allowed + 每个 ws 存在 + 无重复
        if self.work_stations.get(default_work_station_id) is None:
            raise ValueError(f"作业站不存在: {default_work_station_id}")
        if not allowed_work_station_ids:
            raise ValueError(f"工序 {seq} 必须至少指定一个允许作业站")
        if len(set(allowed_work_station_ids)) != len(allowed_work_station_ids):
            raise ValueError(f"工序 {seq} 允许作业站列表存在重复")
        if default_work_station_id not in allowed_work_station_ids:
            raise ValueError(f"工序 {seq} 默认作业站必须在允许作业站列表内")
        for ws_id in allowed_work_station_ids:
            if self.work_stations.get(ws_id) is None:
                raise ValueError(f"作业站不存在: {ws_id}")
        # seq 唯一（同 routing 内）
        existing = self.routings.operations_of(routing_id)
        for o in existing:
            if o.id != exclude_op_id and o.seq == seq:
                raise ValueError(f"工序 seq={seq} 与已有工序冲突")
        # 技能等级校验（沿用 P2c）
        if required_skill_id is not None:
            skill = self.skills.get(required_skill_id)
            if skill is None:
                raise ValueError(f"技能不存在: {required_skill_id}")
            if required_level is None or required_level < 1:
                raise ValueError(f"工序 {seq} 设置了技能要求，必须填写要求等级(>=1)")
            if required_level > skill.max_level:
                raise ValueError(f"工序 {seq} 要求等级超过技能最高等级")
        # code 唯一（同 routing 内，uq_operation_routing_code）
        for o in existing:
            if o.id != exclude_op_id and o.code == code:
                raise ValueError(f"工序码 {code} 与已有工序冲突")

    def update_operation(self, operation_id: int, *, seq, code, name,
                         default_work_station_id, allowed_work_station_ids,
                         required_skill_id, required_level, is_mandatory=True) -> Operation:
        from lightmes.modules.masterdata.models import Operation
        op = self.db.get(Operation, operation_id)
        if op is None:
            raise ValueError(f"工序不存在: {operation_id}")
        self._check_no_work_order(op.routing_id)
        code = code.strip(); name = name.strip()
        self._validate_op_fields(op.routing_id, seq, code, name,
                                 default_work_station_id, allowed_work_station_ids,
                                 required_skill_id, required_level, exclude_op_id=operation_id)
        op.seq = seq; op.code = code; op.name = name
        op.default_work_station_id = default_work_station_id
        op.is_mandatory = is_mandatory
        op.required_skill_id = required_skill_id
        op.required_level = required_level
        # 重写关联表
        self.op_work_stations.delete_by_operation(operation_id)
        for ws_id in allowed_work_station_ids:
            self.op_work_stations.add(operation_id, ws_id)
        self.db.flush()
        return op

    def add_operation(self, routing_id: int, *, seq, code, name,
                      default_work_station_id, allowed_work_station_ids,
                      required_skill_id, required_level, is_mandatory=True) -> Operation:
        from lightmes.modules.masterdata.models import Operation
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        self._check_no_work_order(routing_id)
        code = code.strip(); name = name.strip()
        self._validate_op_fields(routing_id, seq, code, name,
                                 default_work_station_id, allowed_work_station_ids,
                                 required_skill_id, required_level)
        op = Operation(routing_id=routing_id, seq=seq, code=code, name=name,
                       default_work_station_id=default_work_station_id,
                       is_mandatory=is_mandatory,
                       required_skill_id=required_skill_id,
                       required_level=required_level)
        self.db.add(op); self.db.flush()
        for ws_id in allowed_work_station_ids:
            self.op_work_stations.add(op.id, ws_id)
        self.db.flush()
        return op

    def delete_operation(self, operation_id: int) -> None:
        from lightmes.modules.masterdata.models import Operation
        op = self.db.get(Operation, operation_id)
        if op is None:
            raise ValueError(f"工序不存在: {operation_id}")
        self._check_no_work_order(op.routing_id)
        self.op_work_stations.delete_by_operation(operation_id)
        self.db.delete(op); self.db.flush()

    def delete_routing(self, routing_id: int) -> None:
        routing = self.routings.get(routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {routing_id}")
        self._check_no_work_order(routing_id)
        # 先删 operations（关联表跟随 CASCADE），再删 routing
        for op in self.routings.operations_of(routing_id):
            self.op_work_stations.delete_by_operation(op.id)
            self.db.delete(op)
        self.db.flush()
        self.routings.delete(routing_id)
```

- [ ] **Step 6: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/masterdata/test_routing_edit.py -v` → 13 PASS。
全量回归 → 全绿（既有 masterdata 测试不受影响——新方法是叠加，未改既有签名）。
```bash
git add src/lightmes/modules/production/repository.py src/lightmes/modules/masterdata/repository.py src/lightmes/modules/masterdata/service.py tests/modules/masterdata/test_routing_edit.py
git commit -m "feat: routing edit service layer + work-order reference guard"
```

---

### Task 2: API 路由 + 详情页 GET 渲染

**Files:**
- Modify: `src/lightmes/modules/masterdata/router.py`（7 个新路由）
- Create: `src/lightmes/templates/masterdata/routing_detail.html`（最小占位，Task 3 替换为完整 UI）
- Test: `tests/modules/masterdata/test_routing_detail_pages.py`

**Interfaces:**
- Consumes: Task 1 的 6 个 service 方法；`MasterDataQueryService.get_allowed_work_stations`（P2g Task 2）；`SkillService.list_skills`（既有）。
- Produces:
  - `GET /masterdata/routings/{routing_id}` → routing_detail.html（含 routing + operations（每个带 allowed_work_stations list[str]）+ work_stations + skills）
  - `POST /masterdata/routings/{routing_id}` → 改头（name）
  - `POST /masterdata/routings/{routing_id}/status` → 切状态（status）
  - `POST /masterdata/routings/{routing_id}/operations` → 加工序
  - `POST /masterdata/routings/{routing_id}/operations/{operation_id}` → 改工序
  - `POST /masterdata/routings/{routing_id}/operations/{operation_id}/delete` → 删工序
  - `POST /masterdata/routings/{routing_id}/delete` → 删路线（302 回 /masterdata/routings）

- [ ] **Step 1: 写失败测试**

`tests/modules/masterdata/test_routing_detail_pages.py`:
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


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="ed", password="pw12345", display_name="Ed"))
    db_session.flush()
    client.post("/login", data={"username": "ed", "password": "pw12345"})


def _setup(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
    ]))
    db_session.flush()
    return routing, (ws1, ws2)


def test_detail_page_renders(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.get(f"/masterdata/routings/{routing.id}")
    assert resp.status_code == 200
    assert "RT" in resp.text and "工序10" in resp.text


def test_detail_requires_login(client, db_session):
    routing, wss = _setup(db_session)
    resp = client.get(f"/masterdata/routings/{routing.id}")
    assert resp.status_code == 401


def test_update_head_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}", data={"name": "新名"})
    assert resp.status_code == 200
    db_session.refresh(routing)
    assert routing.name == "新名"


def test_set_status_active(client, db_session):
    routing, wss = _setup(db_session)
    md = MasterDataService(db_session)
    md.set_routing_status(routing.id, "inactive")  # 先 inactive
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "active"})
    assert resp.status_code == 200
    db_session.refresh(routing)
    assert routing.status == "active"


def test_add_operation_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}/operations",
                       data={"seq": "20", "code": "OP20", "name": "工序20",
                             "op_ws": str(wss[1].id),
                             "op_allowed": str(wss[1].id)})
    assert resp.status_code == 200
    assert "工序20" in resp.text


def test_update_operation_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    op = MasterDataService(db_session).routings.operations_of(routing.id)[0]
    resp = client.post(f"/masterdata/routings/{routing.id}/operations/{op.id}",
                       data={"seq": "15", "code": "OP15", "name": "改名",
                             "op_ws": str(wss[0].id),
                             "op_allowed": f"{wss[0].id},{wss[1].id}"})
    assert resp.status_code == 200
    db_session.refresh(op)
    assert op.name == "改名" and op.seq == 15


def test_delete_operation_submit(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    op = MasterDataService(db_session).routings.operations_of(routing.id)[0]
    resp = client.post(f"/masterdata/routings/{routing.id}/operations/{op.id}/delete")
    assert resp.status_code == 200
    assert MasterDataService(db_session).routings.operations_of(routing.id) == []


def test_delete_routing_redirects_to_list(client, db_session):
    routing, wss = _setup(db_session)
    _login(client, db_session)
    resp = client.post(f"/masterdata/routings/{routing.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/masterdata/routings"
```

- [ ] **Step 2: 运行确认失败**

Run: `... uv run pytest tests/modules/masterdata/test_routing_detail_pages.py -v`
Expected: FAIL（路由未建）。

- [ ] **Step 3: 写 7 个路由**

在 `masterdata/router.py` 加（顶部 import 补 `from lightmes.modules.masterdata.query_service import MasterDataQueryService` 若未 import；`Operation` 模型 import 按需）：
```python
@router.get("/masterdata/routings/{routing_id}", response_class=HTMLResponse)
def routing_detail_page(
    request: Request, routing_id: int, db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = MasterDataService(db)
    query = MasterDataQueryService(db)
    routing = svc.routings.get(routing_id)
    if routing is None:
        return HTMLResponse("路线不存在", status_code=404)
    operations = svc.routings.operations_of(routing_id)
    op_views = []
    for op in operations:
        allowed_ws = query.get_allowed_work_stations(op.id)
        op_views.append({
            "op": op,
            "allowed_ws_ids": [w.id for w in allowed_ws],
        })
    product = svc.products.get(routing.product_id)
    return templates.TemplateResponse(
        request, "masterdata/routing_detail.html",
        {"routing": routing, "product": product, "op_views": op_views,
         "work_stations": svc.work_stations.list_all(),
         "skills": SkillService(db).list_skills()})


def _parse_allowed(allowed_str: str, default_ws: int) -> list[int]:
    """逗号分隔 ws_id 串 → list[int]，去重保序；空则 [default_ws]。"""
    if allowed_str.strip():
        ids = [int(x) for x in allowed_str.split(",") if x.strip().isdigit()]
    else:
        ids = [default_ws]
    return list(dict.fromkeys(ids))


@router.post("/masterdata/routings/{routing_id}", response_class=HTMLResponse)
def routing_update_head(
    request: Request, routing_id: int, name: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        MasterDataService(db).update_routing_head(routing_id, name)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(str(e))}</div>')
    return HTMLResponse(f'<div style="color:green">✓ 已保存路线头</div>')


@router.post("/masterdata/routings/{routing_id}/status", response_class=HTMLResponse)
def routing_set_status(
    request: Request, routing_id: int, status: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        MasterDataService(db).set_routing_status(routing_id, status)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(str(e))}</div>')
    return HTMLResponse(f'<div style="color:green">✓ 状态已更新为 {escape(status)}</div>')


@router.post("/masterdata/routings/{routing_id}/operations", response_class=HTMLResponse)
def routing_add_operation(
    request: Request, routing_id: int,
    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
    op_ws: int = Form(...), op_allowed: str = Form(""),
    op_skill: str = Form(""), op_level: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    allowed_ids = _parse_allowed(op_allowed, op_ws)
    try:
        MasterDataService(db).add_operation(
            routing_id, seq=seq, code=code, name=name,
            default_work_station_id=op_ws, allowed_work_station_ids=allowed_ids,
            required_skill_id=int(op_skill) if op_skill.strip() else None,
            required_level=int(op_level) if op_level.strip() else None,
            is_mandatory=True)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(str(e))}</div>')
    # 成功后重新渲染详情页（含新工序）
    return routing_detail_page(request, routing_id, db)


@router.post("/masterdata/routings/{routing_id}/operations/{operation_id}", response_class=HTMLResponse)
def routing_update_operation(
    request: Request, routing_id: int, operation_id: int,
    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
    op_ws: int = Form(...), op_allowed: str = Form(""),
    op_skill: str = Form(""), op_level: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    allowed_ids = _parse_allowed(op_allowed, op_ws)
    try:
        MasterDataService(db).update_operation(
            operation_id, seq=seq, code=code, name=name,
            default_work_station_id=op_ws, allowed_work_station_ids=allowed_ids,
            required_skill_id=int(op_skill) if op_skill.strip() else None,
            required_level=int(op_level) if op_level.strip() else None,
            is_mandatory=True)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(str(e))}</div>')
    return routing_detail_page(request, routing_id, db)


@router.post("/masterdata/routings/{routing_id}/operations/{operation_id}/delete", response_class=HTMLResponse)
def routing_delete_operation(
    request: Request, routing_id: int, operation_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        MasterDataService(db).delete_operation(operation_id)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(str(e))}</div>')
    return routing_detail_page(request, routing_id, db)


@router.post("/masterdata/routings/{routing_id}/delete")
def routing_delete(
    request: Request, routing_id: int, db: Session = Depends(get_db),
):
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        MasterDataService(db).delete_routing(routing_id)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {escape(str(e))}</div>', status_code=200)
    return Response(status_code=303, headers={"Location": "/masterdata/routings"})
```
（顶部 import 补 `from markupsafe import escape`、`from lightmes.modules.masterdata.skill_service import SkillService` 若未 import。）

- [ ] **Step 4: 写最小占位详情页模板**

`src/lightmes/templates/masterdata/routing_detail.html`（Task 3 替换为完整 UI）：
```html
{% extends "base.html" %}
{% block title %}路线详情{% endblock %}
{% block content %}
<h1 class="page-title">路线 {{ routing.code }}</h1>
<div class="card">
  <div class="card__title">路线头</div>
  <div>code: {{ routing.code }} | name: {{ routing.name }} | status: {{ routing.status }}</div>
</div>
<div class="card">
  <div class="card__title">工序</div>
  <ul>
    {% for ov in op_views %}
    <li>{{ ov.op.seq }} {{ ov.op.code }} {{ ov.op.name }} (default={{ ov.op.default_work_station_id }}, allowed_ids={{ ov.allowed_ws_ids }})</li>
    {% endfor %}
  </ul>
</div>
{% endblock %}
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/masterdata/test_routing_detail_pages.py -v` → 8 PASS。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata/router.py src/lightmes/templates/masterdata/routing_detail.html tests/modules/masterdata/test_routing_detail_pages.py
git commit -m "feat: routing detail routes (GET + head/status/op CRUD + delete)"
```

---

### Task 3: 详情页完整 UI 模板 + 列表入口

**Files:**
- Modify: `src/lightmes/templates/masterdata/routing_detail.html`（完整 UI）
- Modify: `src/lightmes/templates/masterdata/routings.html`（列表 code 列加详情链接）
- Modify: `tests/modules/masterdata/test_routing_detail_pages.py`（加 2 个渲染断言）

**Interfaces:**
- Consumes: Task 2 的 7 个路由；`op_views` (op + allowed_ws_ids) + routing + product + work_stations + skills 上下文。
- Produces: 完整可编辑详情页（路线头卡片 + 工序表格每行 form + allowed 复选框 + 危险删除按钮 + 添加工序区）。

- [ ] **Step 1: 加渲染断言到既有测试**

在 `test_routing_detail_pages.py::test_detail_page_renders` 末尾追加：
```python
    assert "路线头" in resp.text or "RT" in resp.text  # 路线头卡片渲染
    assert "默认作业站" in resp.text  # 工序表格头
    assert "删除路线" in resp.text  # 危险按钮
```

- [ ] **Step 2: 运行确认失败**

Run: `... uv run pytest tests/modules/masterdata/test_routing_detail_pages.py::test_detail_page_renders -v`
Expected: FAIL（占位模板无"删除路线"/"默认作业站"）。

- [ ] **Step 3: 写完整详情页模板**

完整替换 `src/lightmes/templates/masterdata/routing_detail.html`：
```html
{% extends "base.html" %}
{% block title %}路线详情 · {{ routing.code }}{% endblock %}
{% block container_class %}container--wide{% endblock %}
{% block content %}
<h1 class="page-title">路线详情 · <small>{{ routing.code }} {{ routing.name }}</small></h1>

<div class="card">
  <div class="card__title">路线头</div>
  <form class="form-row" hx-post="/masterdata/routings/{{ routing.id }}" hx-target="#head-result" hx-swap="innerHTML">
    <div class="field"><label>编码</label><input value="{{ routing.code }}" disabled></div>
    <div class="field"><label>产品</label><input value="{{ product.code }} {{ product.name }}" disabled></div>
    <div class="field" style="flex:1"><label>名称</label><input name="name" value="{{ routing.name }}" required></div>
    <button type="submit">保存头</button>
  </form>
  <div class="form-row" style="margin-top:8px">
    <div class="field"><label>状态</label><input value="{{ routing.status }}" disabled></div>
    {% if routing.status == "active" %}
    <form hx-post="/masterdata/routings/{{ routing.id }}/status" hx-target="#head-result" hx-swap="innerHTML"
          style="align-self:flex-end">
      <input type="hidden" name="status" value="inactive">
      <button type="submit" class="btn-secondary">设为 inactive</button>
    </form>
    {% else %}
    <form hx-post="/masterdata/routings/{{ routing.id }}/status" hx-target="#head-result" hx-swap="innerHTML"
          style="align-self:flex-end">
      <input type="hidden" name="status" value="active">
      <button type="submit" class="btn-secondary">设为 active</button>
    </form>
    {% endif %}
    <form hx-post="/masterdata/routings/{{ routing.id }}/delete"
          style="align-self:flex-end; margin-left:auto"
          onsubmit="return confirm('确认删除路线 {{ routing.code }}？该操作不可恢复。')">
      <button type="submit" style="background:#d9534f;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer">删除路线</button>
    </form>
  </div>
  <div id="head-result" class="result-slot"></div>
</div>

<div class="card">
  <div class="card__title">工序（每行单独保存）</div>
  <table class="data-table routing-ops">
    <thead><tr>
      <th style="width:60px">seq</th>
      <th style="width:110px">工序码</th>
      <th>工序名</th>
      <th style="width:180px">默认作业站</th>
      <th style="min-width:240px">允许作业站（勾选）</th>
      <th style="width:140px">技能</th>
      <th style="width:80px">要求等级</th>
      <th style="width:140px">操作</th>
    </tr></thead>
    <tbody>
      {% for ov in op_views %}
      <tr class="op-row">
        <form hx-post="/masterdata/routings/{{ routing.id }}/operations/{{ ov.op.id }}"
              hx-target="#ops-result" hx-swap="innerHTML"
              onsubmit="return confirm('确认保存对该工序的修改？')">
          <td><input name="seq" type="number" value="{{ ov.op.seq }}" style="width:100%"></td>
          <td><input name="code" value="{{ ov.op.code }}" style="width:100%"></td>
          <td><input name="name" value="{{ ov.op.name }}" style="width:100%"></td>
          <td>
            <select name="op_ws" class="op-default-ws" style="width:100%">
              {% for w in work_stations %}<option value="{{ w.id }}" {% if w.id == ov.op.default_work_station_id %}selected{% endif %}>{{ w.code }} {{ w.name }}</option>{% endfor %}
            </select>
          </td>
          <td>
            <div class="op-allowed-cbs">
              {% for w in work_stations %}
              <label class="cb"><input type="checkbox" class="op-allowed-cb" value="{{ w.id }}" {% if w.id in ov.allowed_ws_ids %}checked{% endif %}>
                <span>{{ w.code }} {{ w.name }}</span></label>
              {% endfor %}
            </div>
            <input type="hidden" name="op_allowed" class="op-allowed-hidden" value="{{ ov.allowed_ws_ids|join(',') }}">
          </td>
          <td>
            <select name="op_skill" style="width:100%">
              <option value="">无</option>
              {% for s in skills %}<option value="{{ s.id }}" {% if ov.op.required_skill_id == s.id %}selected{% endif %}>{{ s.name }}</option>{% endfor %}
            </select>
          </td>
          <td><input name="op_level" type="number" min="1" value="{{ ov.op.required_level or '' }}" style="width:100%"></td>
          <td style="white-space:nowrap">
            <button type="submit">保存</button>
          </td>
        </form>
        <td style="white-space:nowrap">
          <form hx-post="/masterdata/routings/{{ routing.id }}/operations/{{ ov.op.id }}/delete"
                hx-target="#ops-result" hx-swap="innerHTML"
                onsubmit="return confirm('确认删除工序 {{ ov.op.code }}？')">
            <button type="submit" style="background:#d9534f;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer">删除</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  <div id="ops-result" class="result-slot"></div>
  <p class="nav-card__desc" style="margin-top:8px">保存/删除工序后整页刷新（HTMX 替换工序列表）。</p>
</div>

<div class="card">
  <div class="card__title">新增工序</div>
  <table class="data-table routing-ops">
    <thead><tr>
      <th style="width:60px">seq</th>
      <th style="width:110px">工序码</th>
      <th>工序名</th>
      <th style="width:180px">默认作业站</th>
      <th style="min-width:240px">允许作业站（勾选）</th>
      <th style="width:140px">技能</th>
      <th style="width:80px">要求等级</th>
      <th style="width:80px">操作</th>
    </tr></thead>
    <tbody>
      <tr class="op-row">
        <form hx-post="/masterdata/routings/{{ routing.id }}/operations"
              hx-target="#ops-result" hx-swap="innerHTML">
          <td><input name="seq" type="number" placeholder="30" style="width:100%"></td>
          <td><input name="code" placeholder="OP30" style="width:100%"></td>
          <td><input name="name" placeholder="工序名" style="width:100%"></td>
          <td>
            <select name="op_ws" class="op-default-ws op-default-ws-new" style="width:100%">
              <option value="">--</option>
              {% for w in work_stations %}<option value="{{ w.id }}">{{ w.code }} {{ w.name }}</option>{% endfor %}
            </select>
          </td>
          <td>
            <div class="op-allowed-cbs">
              {% for w in work_stations %}
              <label class="cb"><input type="checkbox" class="op-allowed-cb" value="{{ w.id }}">
                <span>{{ w.code }} {{ w.name }}</span></label>
              {% endfor %}
            </div>
            <input type="hidden" name="op_allowed" class="op-allowed-hidden" value="">
          </td>
          <td>
            <select name="op_skill" style="width:100%">
              <option value="">无</option>
              {% for s in skills %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}
            </select>
          </td>
          <td><input name="op_level" type="number" min="1" style="width:100%"></td>
          <td><button type="submit">新增</button></td>
        </form>
      </tr>
    </tbody>
  </table>
</div>

<style>
.routing-ops .op-allowed-cbs {
  display: flex; flex-direction: column; gap: 2px;
  max-height: 120px; overflow-y: auto; font-size: 12px;
}
.routing-ops .cb {
  display: flex; align-items: center; gap: 6px;
  cursor: pointer; padding: 2px 6px; border-radius: 4px;
}
.routing-ops .cb input[type="checkbox"] { margin: 0; }
.routing-ops .cb:has(input:checked) {
  background: var(--mint-200, #c5e5d4); font-weight: 600;
}
.routing-ops td input[type="text"],
.routing-ops td input:not([type]),
.routing-ops td input[type="number"],
.routing-ops td select { padding: 4px 6px; font-size: 13px; }
</style>

<script>
// 工序行：默认站 change → 自动勾选对应复选框 + 同步隐藏 input
function sync_allowed(row) {
  var cbs = row.querySelectorAll('.op-allowed-cb');
  var chosen = Array.from(cbs).filter(function(cb){return cb.checked;}).map(function(cb){return cb.value;});
  var defaultSel = row.querySelector('.op-default-ws');
  if (defaultSel.value && chosen.indexOf(defaultSel.value) === -1) {
    chosen.push(defaultSel.value);
    var defCb = row.querySelector('.op-allowed-cb[value="'+defaultSel.value+'"]');
    if (defCb) defCb.checked = true;
  }
  row.querySelector('.op-allowed-hidden').value = chosen.join(',');
}
document.querySelectorAll('.routing-ops .op-default-ws').forEach(function(sel) {
  sel.addEventListener('change', function(){
    var row = this.closest('.op-row');
    if (this.value) {
      var defCb = row.querySelector('.op-allowed-cb[value="'+this.value+'"]');
      if (defCb) defCb.checked = true;
    }
    sync_allowed(row);
  });
});
document.querySelectorAll('.routing-ops .op-allowed-cb').forEach(function(cb) {
  cb.addEventListener('change', function(){ sync_allowed(this.closest('.op-row')); });
});
</script>
{% endblock %}
```
> 注意：tr 内含多个 form（编辑 + 删除）HTML 不合规（tr 不能直接含 form 子元素，且 form 不能跨 td）。改用每行一个 form 包所有 td 是常见做法但 table 语义不允许。**实际可行方案**：把每行改成 div-based 卡片（不用 table）。让我重写——但为了保持表格视觉一致，用 `display: table` 的 CSS 实现。

修订：详情页工序区改用 div + CSS `display:table/table-row/table-cell`（语义上是 div 但视觉是表格；form 可正常包裹 div 行）：
```html
<div class="ops-grid">
  <div class="ops-row ops-head">
    <div>seq</div><div>工序码</div><div>工序名</div><div>默认作业站</div><div>允许作业站（勾选）</div><div>技能</div><div>要求等级</div><div>操作</div>
  </div>
  {% for ov in op_views %}
  <form class="ops-row" hx-post="/masterdata/routings/{{ routing.id }}/operations/{{ ov.op.id }}"
        hx-target="#ops-result" hx-swap="innerHTML"
        onsubmit="return confirm('确认保存对该工序的修改？')">
    <div><input name="seq" type="number" value="{{ ov.op.seq }}"></div>
    <div><input name="code" value="{{ ov.op.code }}"></div>
    <div><input name="name" value="{{ ov.op.name }}"></div>
    <div>
      <select name="op_ws" class="op-default-ws">
        {% for w in work_stations %}<option value="{{ w.id }}" {% if w.id == ov.op.default_work_station_id %}selected{% endif %}>{{ w.code }} {{ w.name }}</option>{% endfor %}
      </select>
    </div>
    <div>
      <div class="op-allowed-cbs">
        {% for w in work_stations %}
        <label class="cb"><input type="checkbox" class="op-allowed-cb" value="{{ w.id }}" {% if w.id in ov.allowed_ws_ids %}checked{% endif %}><span>{{ w.code }} {{ w.name }}</span></label>
        {% endfor %}
      </div>
      <input type="hidden" name="op_allowed" class="op-allowed-hidden" value="{{ ov.allowed_ws_ids|join(',') }}">
    </div>
    <div>
      <select name="op_skill">
        <option value="">无</option>
        {% for s in skills %}<option value="{{ s.id }}" {% if ov.op.required_skill_id == s.id %}selected{% endif %}>{{ s.name }}</option>{% endfor %}
      </select>
    </div>
    <div><input name="op_level" type="number" min="1" value="{{ ov.op.required_level or '' }}"></div>
    <div style="white-space:nowrap">
      <button type="submit">保存</button>
      <button type="button" class="op-delete-btn"
              onclick="if(confirm('确认删除工序 {{ ov.op.code }}？')){this.form.action='/masterdata/routings/{{ routing.id }}/operations/{{ ov.op.id }}/delete';this.form.submit()}">删除</button>
    </div>
  </form>
  {% endfor %}
  <form class="ops-row" hx-post="/masterdata/routings/{{ routing.id }}/operations"
        hx-target="#ops-result" hx-swap="innerHTML">
    <div><input name="seq" type="number" placeholder="30"></div>
    <div><input name="code" placeholder="OP30"></div>
    <div><input name="name" placeholder="工序名"></div>
    <div>
      <select name="op_ws" class="op-default-ws">
        <option value="">--</option>
        {% for w in work_stations %}<option value="{{ w.id }}">{{ w.code }} {{ w.name }}</option>{% endfor %}
      </select>
    </div>
    <div>
      <div class="op-allowed-cbs">
        {% for w in work_stations %}
        <label class="cb"><input type="checkbox" class="op-allowed-cb" value="{{ w.id }}"><span>{{ w.code }} {{ w.name }}</span></label>
        {% endfor %}
      </div>
      <input type="hidden" name="op_allowed" class="op-allowed-hidden" value="">
    </div>
    <div>
      <select name="op_skill">
        <option value="">无</option>
        {% for s in skills %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}
      </select>
    </div>
    <div><input name="op_level" type="number" min="1"></div>
    <div><button type="submit">新增</button></div>
  </form>
</div>
```
> 删除工序用同一个 form（编辑 form）通过 JS 临时改 action 提交——避免 form 嵌套。或者用独立的"删除"通过 onclick 触发原生 form 提交到 delete URL（不通过 HTMX，整页刷新）。**简化**：删除工序就用独立 `<form>` 在表格外用 button——但表格行内不允许嵌套 form。**最终方案**：删除按钮用 `<button type="button">` + onclick `fetch()` POST 到 delete URL，然后 HTMX 触发整页刷新（`htmx.trigger(document.body,'refresh')` 或 `location.reload()`）。最简单稳定：删除按钮 onclick → `if(confirm) location.href='/masterdata/routings/{{routing.id}}/operations/{{ov.op.id}}/delete?_method=POST'`？但路由是 POST。用 fetch：
```javascript
onclick="if(confirm('确认删除工序 {{ ov.op.code }}？')){fetch('/masterdata/routings/{{ routing.id }}/operations/{{ ov.op.id }}/delete',{method:'POST'}).then(()=>location.reload())}"
```
这个方案最干净——删除按钮是独立 button（不在 form 里），fetch POST + reload。详情页其它 form 用 HTMX。

**CSS**（追加到模板 `<style>`）：
```css
.ops-grid { display: table; width: 100%; border-collapse: collapse; }
.ops-row { display: table-row; }
.ops-head { font-weight: 600; background: var(--mint-50, #f0f7f3); }
.ops-row > div { display: table-cell; padding: 6px 4px; border-bottom: 1px solid #e7ece9; vertical-align: middle; }
.ops-head > div { padding: 8px 4px; font-size: 12px; }
```

- [ ] **Step 4: routings.html 列表 code 列加详情链接**

读 `src/lightmes/templates/masterdata/routings.html` 列表表格（约 38-49 行），把 code 单元格改成链接：
```html
<td><a href="/masterdata/routings/{{ r.id }}">{{ r.code }}</a></td>
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/masterdata/test_routing_detail_pages.py -v` → 全部 PASS。
全量回归 → 全绿。
```bash
git add src/lightmes/templates/masterdata/routing_detail.html src/lightmes/templates/masterdata/routings.html tests/modules/masterdata/test_routing_detail_pages.py
git commit -m "feat: routing detail full UI (head card + ops div-grid + checkboxes + danger delete)"
```

---

### Task 4: 端到端 + 终审

**Files:**
- Test: `tests/modules/masterdata/test_routing_detail_pages.py`（加端到端串联 + 工单引用拒端到端）
- Manual smoke：详情页手工验证（操作员视角）

- [ ] **Step 1: 加端到端串联测试**

`tests/modules/masterdata/test_routing_detail_pages.py` 加：
```python
def test_e2e_full_edit_flow(client, db_session):
    """完整编辑流：详情 → 改头 → 切 inactive→active → 加工序 → 改工序 → 删工序 → 删路线"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PE", name="件E", type="finished"))
    line = md.create_line(LineCreate(code="LE", name="线E"))
    ws1 = md.create_work_station(WorkStationCreate(code="WE1", name="站E1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="WE2", name="站E2", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RTE", name="路线E", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id])]))
    db_session.flush()
    _login(client, db_session)

    # 1) 改头
    r = client.post(f"/masterdata/routings/{routing.id}", data={"name": "新名E"})
    assert r.status_code == 200
    db_session.refresh(routing); assert routing.name == "新名E"

    # 2) 切 active → inactive → active
    r = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "inactive"})
    assert r.status_code == 200
    r = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "active"})
    assert r.status_code == 200

    # 3) 加工序
    r = client.post(f"/masterdata/routings/{routing.id}/operations",
                    data={"seq": "20", "code": "OP20", "name": "工序20",
                          "op_ws": str(ws2.id), "op_allowed": str(ws2.id)})
    assert r.status_code == 200 and "工序20" in r.text
    ops = md.routings.operations_of(routing.id)
    assert len(ops) == 2 and any(o.code == "OP20" for o in ops)

    # 4) 改工序（OP20 → allowed 加 ws1）
    op20 = next(o for o in ops if o.code == "OP20")
    r = client.post(f"/masterdata/routings/{routing.id}/operations/{op20.id}",
                    data={"seq": "20", "code": "OP20", "name": "改名20",
                          "op_ws": str(ws2.id), "op_allowed": f"{ws1.id},{ws2.id}"})
    assert r.status_code == 200
    db_session.refresh(op20)
    assert op20.name == "改名20"

    # 5) 删工序 OP10
    op10 = next(o for o in md.routings.operations_of(routing.id) if o.code == "OP10")
    r = client.post(f"/masterdata/routings/{routing.id}/operations/{op10.id}/delete")
    assert r.status_code == 200
    remaining = md.routings.operations_of(routing.id)
    assert len(remaining) == 1 and remaining[0].code == "OP20"

    # 6) 删路线 → 303 回列表
    r = client.post(f"/masterdata/routings/{routing.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert md.routings.get(routing.id) is None


def test_e2e_work_order_blocks_all_writes(client, db_session):
    """工单引用 → 所有写操作拒绝"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PW", name="件W", type="finished"))
    line = md.create_line(LineCreate(code="LW", name="线W"))
    ws1 = md.create_work_station(WorkStationCreate(code="WW1", name="站W1", line_id=line.id, seq=1))
    routing = md.create_routing(RoutingCreate(code="RTW", name="路线W", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SRW", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    prod.create_work_order(WorkOrderCreate(code="WOW", product_id=p.id, routing_id=routing.id,
        line_id=line.id, qty=3, sn_rule_id=rule.id))
    db_session.flush()
    _login(client, db_session)

    # 改头 → 拒
    r = client.post(f"/masterdata/routings/{routing.id}", data={"name": "x"})
    assert "工单" in r.text
    # 切状态 → 拒
    r = client.post(f"/masterdata/routings/{routing.id}/status", data={"status": "inactive"})
    assert "工单" in r.text
    # 删路线 → 拒（HTMLResponse 200 含错误，不是 303）
    r = client.post(f"/masterdata/routings/{routing.id}/delete", follow_redirects=False)
    assert r.status_code == 200 and "工单" in r.text
```

- [ ] **Step 2: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/masterdata/test_routing_detail_pages.py -v` → 全部 PASS（含 2 个新端到端）。
全量回归 → 全绿。
```bash
git add tests/modules/masterdata/test_routing_detail_pages.py
git commit -m "test: routing edit e2e + work-order reference block e2e"
```

- [ ] **Step 3: 手动 smoke 测试**（重启服务后人工点击详情页验证，不写测试）

```bash
# 重启 dev server
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run uvicorn lightmes.main:app --host 127.0.0.1 --port 8080 --reload
```
浏览器访问 `/masterdata/routings`，点列表 code 进详情页，验证：
- 路线头 name 可改 + 保存
- active/inactive 切换按钮工作
- 工序表格每行可改（seq/code/name/默认站/允许复选框/技能/等级）+ 保存
- 新增工序 + 删除工序
- 删除路线 → 回列表

---

## Self-Review 结果

**Spec 覆盖**（对照 spec §4/§5/§6）：
- RoutingRepository.delete / OperationWorkStationRepository.delete_by_operation → Task 1 ✅
- WorkOrderRepository.count_by_routing → Task 1 ✅
- 6 个 MasterDataService 写方法（update_routing_head/set_routing_status/update_operation/add_operation/delete_operation/delete_routing）含工单引用校验 + active 冲突 + default∈allowed + seq/code 冲突 + 技能等级 → Task 1 ✅
- 7 个路由（GET 详情 + 改头 + 切状态 + 加工序 + 改工序 + 删工序 + 删路线）→ Task 2 ✅
- routing_detail.html 完整 UI（路线头 + 工序表格每行 form + allowed 复选框 + 危险按钮 + 新增区）→ Task 3 ✅
- routings.html 列表 code 列加详情链接 → Task 3 ✅
- 端到端测试（完整编辑流 + 工单引用拒）→ Task 4 ✅
- 物理删除级联（删 routing → operations + 关联表清）→ Task 1 测试覆盖 ✅

**占位符扫描**：所有 code step 含完整代码。Task 3 Step 3 的 tr-form 嵌套问题已识别并改用 div + CSS display:table 方案；删除按钮用 fetch + reload（不嵌套 form）。

**类型一致性**：`WorkOrderRepository.count_by_routing(routing_id) -> int`、`RoutingRepository.delete(routing_id)`、`OperationWorkStationRepository.delete_by_operation(op_id)`、`MasterDataService.{update_routing_head/set_routing_status/update_operation/add_operation/delete_operation/delete_routing}` —— 定义处（Task 1）与引用处（Task 2）一致 ✅。

**关键回归风险**：本计划全部是叠加（新方法 + 新路由 + 新模板），不动既有 create_routing / 既有 list 页面 / 既有 P2g 多对多逻辑。Task 1 全量回归应绿。
