# P2f 工位主界面入口一站式重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工位作业交互从"select-wo + bind-and-pass + load"多片段拼接，收敛为一张就绪页（作业站下拉+可用工单下拉+扫码框）→ 一次进入即渲染 P2d 富主界面（顶部状态栏/工艺路径全景/物料绑定/参数/SOP/PASS）；首站扫载体码**只绑 SN、不自动过首工序**，由操作员手动 PASS 才过站。

**Architecture:** 单页三栏入口 + 新 `POST /production/station/enter`（三路 scan 判定：SN/活跃载体码/首站新载体码）+ 新只读端点 `GET /production/station/work-orders`（按作业站联动）；CarrierService 拆 `bind_first_carrier`（仅绑不过站）替代 `bind_and_pass_first`；`pass_operation`/`StationService.load` 不改；下线 select-wo/bind-and-pass 路由与模板。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0 风格；repository 只 flush；事务边界 get_db（请求层 commit/rollback）。
- **首站扫载体码语义**：只绑 SN（写 `carrier_code`+`CarrierBinding`），**不**调 `pass_operation`、**不**生成 `OperationRecord`。status 仍 "pending"。由操作员手动 PASS 触发首工序过站。
- **三路 scan 判定顺序**（enter）：先 `get_by_sn`（SN 命中）→ 否则 `get_active_by_carrier`（活跃载体码命中）→ 否则视为首站新载体码，调 `bind_first_carrier(work_order_id, scan, operator_id)` 绑 SN 后用 `StationService.load` 组装富界面。
- operator_id **服务端赋值**（防伪造，沿用 P2d/P2e）：写路由取 `current_user.id`，Form 不声明 operator_id 字段。
- 写路由 require_login：`current_user_or_none is None → Response(401, HX-Redirect /login)`；DomainError → `db.rollback()` + 错误片段（保留 `work_station_id` 上下文）。
- 工单下拉仅显示 `selectable_for_station(ws.line_id)`（released/in_process 且本产线）+ 每条剩余 pending 数。
- NO CDN；Jinja2 `{{ }}` 自动转义；HTMX 片段插值用 markupsafe.escape（如手动拼接）。
- `pass_operation`/`StationService.load`/`SnGenerator` 不改；无新表/无迁移。
- 提交前缀 `feat:`/`refactor:`/`test:`/`chore:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。
- **PASS 后行为**（产品决策默认）：渲染重置片段，提示扫下一单元，**保持当前工单上下文**（工单下拉不变，继续扫同工单的下一载体码，直到该工单 pending 用完）。
- **change 清空**：操作员改作业站或工单时，JS 清空 `#station-root`（避免看到陈旧富界面）。

---

## File Structure

P2f 结束时新增/修改/删除：

```
src/lightmes/modules/production/
├── carrier_service.py   # 改：bind_and_pass_first → bind_first_carrier（仅绑不过站）
├── router.py            # 改：新 POST /station/enter + 新 GET /station/work-orders；删 select-wo + bind-and-pass
├── schemas.py           # 不改
└── station_service.py   # 不改
src/lightmes/templates/production/
├── station.html                              # 改：三栏入口（作业站下拉+工单下拉+扫码）+ 联动 JS
├── station_view.html                         # 不改（P2d 富主界面复用）
├── partials/station_pass_result.html         # 改：重置片段保持工单上下文（已带上 work_station_id）
├── partials/station_wo_options.html          # 新：仅 <option> 片段（工单下拉联动）
├── partials/station_enter_error.html         # 新：enter 失败红片段
├── partials/station_wo_selected.html         # 删（被新流程取代）
└── partials/station_bind_result.html         # 删（被新流程取代）
src/lightmes/modules/masterdata/query_service.py  # 已有 list_work_stations（P2e 已加，复用）
src/lightmes/modules/production/repository.py     # 已有 selectable_for_station / count_pending_by_work_order（P2e 已加，复用）
tests/modules/production/
├── test_carrier_service.py             # 改：删 bind_and_pass_first 测试，加 bind_first_carrier 测试
├── test_station_carrier_pages.py       # 改：删 select-wo/bind-and-pass 测试，加 enter/work-orders 测试
└── test_station_main_flow.py           # 新：端到端（选站→选工单→扫载体码→富界面→PASS→重置）
```

---

### Task 1: CarrierService 拆分（bind_first_carrier 仅绑不过站）

**Files:**
- Modify: `src/lightmes/modules/production/carrier_service.py`
- Test: `tests/modules/production/test_carrier_service.py`

**Interfaces:**
- Consumes: `SerialUnitRepository.first_pending_by_work_order/get_active_by_carrier`；`CarrierBindingRepository.add`；`CarrierBinding` model。
- Produces: `CarrierService.bind_first_carrier(work_order_id: int, carrier_code: str, operator_id: int | None) -> SerialUnit`（仅绑 SN 不过站）。删除 `bind_and_pass_first`。

- [ ] **Step 1: 写失败测试（替换 bind_and_pass_first 相关）**

在 `tests/modules/production/test_carrier_service.py` 把原 `bind_and_pass_first` 相关测试改为 `bind_first_carrier`。完整新测试块：
```python
from lightmes.modules.production.repository import OperationRecordRepository


def test_bind_first_carrier_assigns_first_pending_without_passing(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    su1 = svc.bind_first_carrier(wo.id, "PAL-1", user.id)
    su2 = svc.bind_first_carrier(wo.id, "PAL-2", user.id)
    # 顺序赋值：第一个 pending → PAL-1，第二个 → PAL-2
    assert su1.sn == "SN00001" and su2.sn == "SN00002"
    # 仅绑、不过站：status 仍 pending；carrier_code 已设；有活跃 binding
    assert su1.status == "pending" and su1.carrier_code == "PAL-1"
    assert CarrierBindingRepository(db_session).active_by_serial_unit(su1.id) is not None
    # 关键：无 OperationRecord（证明没过首工序）
    assert OperationRecordRepository(db_session).list_by_serial_unit(su1.id) == []


def test_bind_first_carrier_exhausted_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    svc.bind_first_carrier(wo.id, "PAL-1", user.id)
    with pytest.raises(BusinessRuleError):  # pending 用完
        svc.bind_first_carrier(wo.id, "PAL-2", user.id)


def test_bind_first_carrier_duplicate_carrier_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_first_carrier(wo.id, "PAL-DUP", user.id)
    with pytest.raises(BusinessRuleError):  # 载体码已绑活跃单元
        svc.bind_first_carrier(wo.id, "PAL-DUP", user.id)


def test_unbind_after_bind_first_carrier_allows_reuse(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_first_carrier(wo.id, "PAL-R", user.id)
    su = svc.unbind("PAL-R", user.id)
    assert su.carrier_code is None
    # 载体码可复用
    su2 = svc.bind_first_carrier(wo.id, "PAL-R", user.id)
    assert su2.sn == "SN00002"


def test_unbind_by_sn(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    su = svc.bind_first_carrier(wo.id, "PAL-X", user.id)
    su_unbound = svc.unbind(su.sn, user.id)
    assert su_unbound.carrier_code is None


def test_unbind_unknown_raises(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    with pytest.raises(NotFoundError):
        svc.unbind("NOPE", user.id)


def test_selectable_for_station_filters(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    sel = WorkOrderRepository(db_session).selectable_for_station(line.id)
    assert wo.id in [w.id for w in sel]
    from lightmes.modules.masterdata.service import MasterDataService as MD
    other = MD(db_session).create_line(LineCreate(code="OTH", name="别线"))
    db_session.flush()
    assert WorkOrderRepository(db_session).selectable_for_station(other.id) == []
```
保留文件顶部 imports（含 `pytest`、`LineCreate`、`BusinessRuleError`、`NotFoundError`、`SerialUnitRepository`、`CarrierBindingRepository`、`WorkOrderRepository`）。`_setup` 辅助保留（与 P2e Task 4 一致，含 n_ops=2 默认）。

- [ ] **Step 2: 运行确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_carrier_service.py -v`
Expected: FAIL（`AttributeError: bind_first_carrier` 或 `ImportError`，旧 `bind_and_pass_first` 测试已删）。

- [ ] **Step 3: 拆分 CarrierService**

完整替换 `src/lightmes/modules/production/carrier_service.py`：
```python
from datetime import datetime

from sqlalchemy.orm import Session

from lightmes.modules.production.models import CarrierBinding, SerialUnit
from lightmes.modules.production.repository import (
    SerialUnitRepository, CarrierBindingRepository,
)
from lightmes.shared.errors import BusinessRuleError, NotFoundError


class CarrierService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.bindings = CarrierBindingRepository(db)

    def bind_first_carrier(
        self, work_order_id: int, carrier_code: str, operator_id: int | None,
    ) -> SerialUnit:
        """首站扫载体码：按顺序取下一个 pending SN 与载体码绑定。

        只绑、不过站：不调 pass_operation、不写 OperationRecord。
        操作员在富主界面手动按 PASS 才过首工序。
        """
        su = self.serial_units.first_pending_by_work_order(work_order_id)
        if su is None:
            raise BusinessRuleError("工单 SN 已全部投产，请选择新工单")
        if self.serial_units.get_active_by_carrier(carrier_code) is not None:
            raise BusinessRuleError(f"载体码已绑定其他产品，请先解绑: {carrier_code}")
        su.carrier_code = carrier_code
        self.bindings.add(CarrierBinding(
            serial_unit_id=su.id, carrier_code=carrier_code, operator_id=operator_id))
        self.db.flush()
        return su

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
            binding.unbound_at = datetime.now()
            binding.unbound_reason = "manual"
        su.carrier_code = None
        self.db.flush()
        return su
```
（移除了对 `OperationPassService`/`OperationPassInput`/`OperationPassResult`/`WorkOrderRepository` 的 import 与 `bind_and_pass_first` 方法本身。）

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/production/test_carrier_service.py -v` → 7 PASS。
**重要**：`/production/station/bind-and-pass` 路由与 `test_station_carrier_pages.py` 中相关测试此刻会因 `bind_and_pass_first` 删除而失败——**这是预期的跨任务状态**，Task 2 会移除该路由与测试。Task 1 只承诺 `test_carrier_service.py` 全绿 + 与 bind_first_carrier 无关的全量回归绿。运行全量，列出预期失败（应为 test_station_carrier_pages.py 中 bind-and-pass 相关用例）。
```bash
git add src/lightmes/modules/production/carrier_service.py tests/modules/production/test_carrier_service.py
git commit -m "refactor: split CarrierService.bind_first_carrier (bind only, no pass)"
```

---

### Task 2: 路由收敛（新 enter + work-orders 端点；删 select-wo/bind-and-pass）

**Files:**
- Modify: `src/lightmes/modules/production/router.py`
- Create: `src/lightmes/templates/production/partials/station_wo_options.html`
- Create: `src/lightmes/templates/production/partials/station_enter_error.html`
- Delete: `src/lightmes/templates/production/partials/station_wo_selected.html`
- Delete: `src/lightmes/templates/production/partials/station_bind_result.html`
- Test: `tests/modules/production/test_station_carrier_pages.py`（重写）

**Interfaces:**
- Consumes: `CarrierService.bind_first_carrier`（Task 1）；`StationService.load`（不改）；`SerialUnitRepository.count_pending_by_work_order`；`WorkOrderRepository.selectable_for_station`；`MasterDataQueryService.get_work_station`；`current_user_or_none`；DomainError→rollback 约定。
- Produces:
  - `POST /production/station/enter`（Form: work_station_id, work_order_id, scan；operator_id=current_user.id 防伪造；三路 scan 判定；成功渲染 station_view.html，失败渲染 station_enter_error.html）
  - `GET /production/station/work-orders?work_station_id=X`（只读，返回 station_wo_options.html 片段）
  - 移除 `POST /production/station/select-wo`、`POST /production/station/bind-and-pass` 路由

- [ ] **Step 1: 重写页面测试**

完整替换 `tests/modules/production/test_station_carrier_pages.py`：
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
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="sc", password="pw12345", display_name="Sc"))
    db_session.flush()
    client.post("/login", data={"username": "sc", "password": "pw12345"})


def _setup(db_session, n_ops=2, qty=2, status_release=True):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id,
        line_id=line.id, qty=qty, sn_rule_id=rule.id))
    if status_release:
        prod.release_work_order(wo.id)
    db_session.flush()
    return ws, wo, line


def test_work_orders_endpoint_returns_options(client, db_session):
    ws, wo, line = _setup(db_session)
    _login(client, db_session)
    resp = client.get(f"/production/station/work-orders?work_station_id={ws[0].id}")
    assert resp.status_code == 200
    assert f'<option value="{wo.id}"' in resp.text
    assert "剩余" in resp.text  # 含剩余 pending 数


def test_work_orders_endpoint_filters_other_line(client, db_session):
    ws, wo, line = _setup(db_session)
    md = MasterDataService(db_session)
    other_line = md.create_line(LineCreate(code="OTH", name="别线"))
    other_ws = md.create_work_station(WorkStationCreate(code="OW", name="别站", line_id=other_line.id, seq=1))
    db_session.flush()
    _login(client, db_session)
    resp = client.get(f"/production/station/work-orders?work_station_id={other_ws.id}")
    assert resp.status_code == 200
    assert f'value="{wo.id}"' not in resp.text  # 异产线工单不在结果


def test_work_orders_requires_login(client, db_session):
    ws, wo, line = _setup(db_session)
    resp = client.get(f"/production/station/work-orders?work_station_id={ws[0].id}")
    assert resp.status_code == 401


def test_enter_first_station_carrier_binds_sn_no_pass(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id),
                             "scan": "PALLET-1"})
    assert resp.status_code == 200
    # 进入主界面（station_view.html）：渲染了工艺路径全景 + PASS 按钮
    assert "确认过站" in resp.text or "工艺路径" in resp.text
    # 关键：SN 已绑载体码，但无 OperationRecord（只绑不过站）
    su = SerialUnitRepository(db_session).get_active_by_carrier("PALLET-1")
    assert su is not None and su.status == "pending"
    assert OperationRecordRepository(db_session).list_by_serial_unit(su.id) == []


def test_enter_downstream_sn_loads_main(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    # 先在首站用载体码绑一件
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-1"})
    su = SerialUnitRepository(db_session).get_active_by_carrier("PALLET-1")
    # 直接手工把 su 推进到工序2 模拟"首工序已过"，后续站扫 SN 应能加载
    from lightmes.modules.production.models import SerialUnit
    db_session.execute(
        __import__("sqlalchemy").update(SerialUnit).where(SerialUnit.id == su.id)
        .values(current_operation_seq=1, status="in_process"))
    db_session.flush()
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[1].id),
                             "work_order_id": str(wo.id), "scan": su.sn})
    assert resp.status_code == 200
    assert "工艺路径" in resp.text or "确认过站" in resp.text


def test_enter_carrier_already_bound_blocks(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-DUP"})
    # 同一载体码再投一件 → 已绑拦截
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id), "scan": "PALLET-DUP"})
    assert resp.status_code == 200
    assert "✗" in resp.text and ("解绑" in resp.text or "已绑" in resp.text)


def test_enter_work_order_exhausted_blocks(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=1)
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-1"})
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id), "scan": "PALLET-2"})
    assert resp.status_code == 200 and "全部投产" in resp.text


def test_enter_requires_login(client, db_session):
    ws, wo, line = _setup(db_session)
    resp = client.post("/production/station/enter",
                       data={"work_station_id": str(ws[0].id),
                             "work_order_id": str(wo.id), "scan": "X"})
    assert resp.status_code == 401
```

- [ ] **Step 2: 运行确认失败**

Run: `... uv run pytest tests/modules/production/test_station_carrier_pages.py -v`
Expected: FAIL（enter/work-orders 路由未建、404/500、bind-and-pass 旧测试已删但路由仍在 router.py）。

- [ ] **Step 3: 改 router.py — 删旧路由、加新路由**

在 `src/lightmes/modules/production/router.py`：
- 删除 `station_select_wo` 与 `station_bind_and_pass` 两个处理器函数（整段）。
- 在 `station_pass` 处理器之后追加两个新处理器：
```python
@router.get("/production/station/work-orders", response_class=HTMLResponse)
def station_work_orders(
    request: Request, work_station_id: int = Query(...), db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    ws = MasterDataQueryService(db).get_work_station(work_station_id)
    if ws is None:
        return HTMLResponse("")  # 作业站不存在 → 空片段
    wo_repo = ProductionService(db).work_orders
    su_repo = SerialUnitRepository(db)
    wo_list = [
        {"id": w.id, "code": w.code, "remaining": su_repo.count_pending_by_work_order(w.id)}
        for w in wo_repo.selectable_for_station(ws.line_id)
    ]
    return templates.TemplateResponse(
        request, "production/partials/station_wo_options.html",
        {"wo_list": wo_list},
    )


@router.post("/production/station/enter", response_class=HTMLResponse)
def station_enter(
    request: Request,
    work_station_id: int = Form(...),
    work_order_id: int = Form(...),
    scan: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    su_repo = SerialUnitRepository(db)
    load_svc = StationService(db)
    try:
        # 三路判定：SN → 活跃载体码 → 首站新载体码（绑 SN）
        su = su_repo.get_by_sn(scan)
        if su is None:
            su = su_repo.get_active_by_carrier(scan)
        if su is None:
            # 首站新载体码：绑 SN（不过站），bind_first_carrier 内部校验
            su = CarrierService(db).bind_first_carrier(work_order_id, scan.strip(), user.id)
        view = load_svc.load(scan.strip() if su.carrier_code == scan.strip() else su.sn,
                             work_station_id, user.id)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_enter_error.html",
            {"error": e.detail, "work_station_id": work_station_id},
        )
    return templates.TemplateResponse(
        request, "production/station_view.html",
        {"view": view, "work_station_id": work_station_id},
    )
```
- 在顶部 imports 追加 `from fastapi import Query`（若未引入）。

> `enter` 里 load 的 scan 参数：绑载体码后 `su.carrier_code == scan`（新绑）→ 用 scan 调 load（load 内部会按载体码命中该 su）；SN/活跃载体码命中 → 用 su.sn 调 load（保证一致）。这样三种来源最终都正确组装 StationView。

- [ ] **Step 4: 新建模板 + 删旧片段**

新建 `src/lightmes/templates/production/partials/station_wo_options.html`：
```html
{% for w in wo_list %}
<option value="{{ w.id }}">{{ w.code }}（剩余 {{ w.remaining }}）</option>
{% endfor %}
```
（空 wo_list 时此片段为空字符串，前端下拉仅剩"— 选择工单 —"占位。）

新建 `src/lightmes/templates/production/partials/station_enter_error.html`：
```html
<div class="alert alert--danger">✗ {{ error }}</div>
<div class="card"><div class="nav-card__desc">请检查作业站/工单/扫码后重新进入。
  返回 <a href="/production/station?work_station_id={{ work_station_id }}">工位作业</a>。</div></div>
```

删除 `src/lightmes/templates/production/partials/station_wo_selected.html`、`station_bind_result.html`。

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/production/test_station_carrier_pages.py -v` → 全部 PASS（8）。
全量回归 → 全绿。
```bash
git add src/lightmes/modules/production/router.py src/lightmes/templates/production/partials/station_wo_options.html src/lightmes/templates/production/partials/station_enter_error.html tests/modules/production/test_station_carrier_pages.py
git rm src/lightmes/templates/production/partials/station_wo_selected.html src/lightmes/templates/production/partials/station_bind_result.html
git commit -m "feat: consolidate station entry into /enter + /work-orders; drop select-wo/bind-and-pass"
```

---

### Task 3: 就绪页三栏 UI + 联动 JS + 端到端测试

**Files:**
- Modify: `src/lightmes/templates/production/station.html`
- Modify: `src/lightmes/templates/production/partials/station_pass_result.html`（确保重置片段带工单上下文，扫下一载体码）
- Test: `tests/modules/production/test_station_main_flow.py`

**Interfaces:**
- Consumes: `GET /production/station/work-orders?work_station_id=X`（Task 2）；`POST /production/station/enter`（Task 2）；既有 `POST /production/station/pass`；`list_work_stations`。
- Produces: 就绪页三栏 + 联动 JS；PASS 后重置片段保持工单上下文。

- [ ] **Step 1: 写端到端失败测试**

新建 `tests/modules/production/test_station_main_flow.py`：
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
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository,
)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session):
    AuthService(db_session).create_user(UserCreate(username="mf", password="pw12345", display_name="Mf"))
    db_session.flush()
    client.post("/login", data={"username": "mf", "password": "pw12345"})


def _setup(db_session, n_ops=2, qty=2):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id,
        line_id=line.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    return ws, wo, line


def test_ready_page_has_three_sections(client, db_session):
    ws, wo, line = _setup(db_session)
    resp = client.get("/production/station")
    assert resp.status_code == 200
    assert "作业站" in resp.text and "工单" in resp.text and "扫" in resp.text


def test_e2e_first_station_bind_view_pass_reset(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=2, qty=2)
    _login(client, db_session)
    # 1) 选作业站→选工单→扫载体码 → 进入主界面
    r1 = client.post("/production/station/enter",
                     data={"work_station_id": str(ws[0].id),
                           "work_order_id": str(wo.id), "scan": "PALLET-1"})
    assert r1.status_code == 200 and "工艺路径" in r1.text
    # 2) 验证只绑、不过站
    su = SerialUnitRepository(db_session).get_active_by_carrier("PALLET-1")
    assert su.status == "pending"
    assert OperationRecordRepository(db_session).list_by_serial_unit(su.id) == []
    # 3) 手动 PASS 首工序
    r2 = client.post("/production/station/pass",
                     data={"work_station_id": str(ws[0].id), "scan": "PALLET-1"})
    assert r2.status_code == 200 and "已过" in r2.text
    # 4) PASS 后 SerialUnit 推进 + 有 OperationRecord
    db_session.refresh(su)
    assert su.current_operation_seq == 1 and su.status == "in_process"
    assert len(OperationRecordRepository(db_session).list_by_serial_unit(su.id)) == 1


def test_pass_result_keeps_work_order_context(client, db_session):
    ws, wo, line = _setup(db_session, n_ops=1, qty=2)  # 单工序，首站即完工
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws[0].id),
                      "work_order_id": str(wo.id), "scan": "PALLET-1"})
    r = client.post("/production/station/pass",
                    data={"work_station_id": str(ws[0].id), "scan": "PALLET-1"})
    assert r.status_code == 200
    # 重置片段带工单上下文：仍有工单号 / work_order_id 供扫下一件
    assert str(wo.id) in r.text or wo.code in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `... uv run pytest tests/modules/production/test_station_main_flow.py -v`
Expected: `test_ready_page_has_three_sections` 可能软通过（当前模板已有三栏雏形）；`test_e2e_first_station_bind_view_pass_reset` 应 PASS（Task 1+2 已铺好链路，PASS 既有）；`test_pass_result_keeps_work_order_context` 可能 FAIL（当前 station_pass_result.html 重置片段可能不携带 wo.id/code）。

- [ ] **Step 3: 重写 station.html 就绪页**

完整替换 `src/lightmes/templates/production/station.html`：
```html
{% extends "base.html" %}
{% block title %}工位作业{% endblock %}
{% block content %}
<h1 class="page-title">工位作业主界面</h1>

<div class="card">
  <div class="card__title">扫码进入</div>
  <form class="form-row" id="enter-form" hx-post="/production/station/enter" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) document.getElementById('enter-scan').value=''">
    <div class="field"><label>作业站</label>
      <select id="station-ws-select" name="work_station_id" required
              onchange="if(this.value){document.getElementById('station-wo-select').setAttribute('hx-vals', JSON.stringify({work_station_id:this.value})); htmx.trigger(document.getElementById('station-wo-select'),'reset-wo'); document.getElementById('station-root').innerHTML='';}">
        <option value="" disabled selected>— 选择作业站 —</option>
        {% for s in station_options %}
        <option value="{{ s.id }}" {% if s.id == work_station_id %}selected{% endif %}>{{ s.label }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="field"><label>工单</label>
      <select id="station-wo-select" name="work_order_id" required
              hx-get="/production/station/work-orders"
              hx-trigger="reset-wo from:body, change"
              hx-target="this"
              hx-swap="innerHTML"
              hx-include="#station-ws-select"
              onchange="document.getElementById('station-root').innerHTML='';">
        <option value="" disabled selected>— 选择工单 —</option>
      </select>
    </div>
    <div class="field" style="flex:1"><label>扫 SN / 载体码</label>
      <input id="enter-scan" name="scan" placeholder="首件扫载体码，后续扫 SN/载体码" required autofocus></div>
    <button type="submit">进入</button>
  </form>
  <div class="nav-card__desc">作业站选定后自动列出本产线可用工单；首件扫载体码即自动绑定 SN 进入主界面，再手动确认过站。</div>
</div>

<div id="station-root" class="station-root"></div>

<script>
  // 页面加载时，若已选作业站，触发工单下拉拉取
  (function () {
    var wsSel = document.getElementById('station-ws-select');
    if (wsSel && wsSel.value) {
      htmx.trigger(document.getElementById('station-wo-select'), 'reset-wo');
    }
  })();
</script>
{% endblock %}
```

> HTMX `hx-include="#station-ws-select"` 让工单下拉的 GET 自动带当前作业站；`reset-wo` 自定义事件在作业站 change 时触发重新拉取。`hx-target="this"` + `hx-swap="innerHTML"` 替换工单 select 的 options（注意：会替换整个 innerHTML 含占位 option，所以 endpoint 必须只返回 options——已如此实现）。

- [ ] **Step 4: 改 station_pass_result.html 保持工单上下文**

读现有 `src/lightmes/templates/production/partials/station_pass_result.html`，把"扫下一单元"表单改为**带 work_order_id 上下文**（让操作员继续扫同工单下一载体码）：
```html
{% if error %}
<div class="alert alert--danger">✗ {{ error }}</div>
<div class="card"><div class="nav-card__desc">返回 <a href="/production/station?work_station_id={{ work_station_id }}">工位作业</a>。</div></div>
{% else %}
<div class="alert alert--ok">
  ✓ <strong>{{ result.sn }}</strong> — 已过 工序{{ result.passed_op.seq }} {{ result.passed_op.name }}
  {% if result.is_finished %}<span class="badge">完工</span>
  {% else %} → 下一站：工序{{ result.next_op.seq }} {{ result.next_op.name }}{% endif %}
  {% if result.bound_count %}<span class="badge">绑定 {{ result.bound_count }} 组件</span>{% endif %}
  {% if result.param_count %}<span class="badge">录 {{ result.param_count }} 参数</span>{% endif %}
</div>
<div class="card">
  <div class="card__title">继续作业</div>
  <form class="form-row" hx-post="/production/station/enter" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=scan]').value=''">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <input type="hidden" name="work_order_id" value="{{ work_order_id }}">
    <div class="field" style="flex:1"><label>扫下一 SN / 载体码</label>
      <input name="scan" placeholder="首件扫载体码，后续扫 SN/载体码" autofocus></div>
    <button type="submit">进入</button>
  </form>
</div>
{% endif %}
```

**关键**：`station_pass` 路由处理器（`router.py` 内）渲染本片段时**必须传入 work_order_id**。读现处理器，确认它是否已传；若未传，需在 `station_pass` 的 success render context 里加上 `"work_order_id": <从 SerialUnit.work_order_id 解析>`。具体改法：
```python
# station_pass 处理器成功分支
wo_id = (SerialUnitRepository(db).get_by_sn(result.sn).work_order_id
         if SerialUnitRepository(db).get_by_sn(result.sn) else None)
return templates.TemplateResponse(
    request, "production/partials/station_pass_result.html",
    {"result": result, "work_station_id": work_station_id, "work_order_id": wo_id},
)
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `... uv run pytest tests/modules/production/test_station_main_flow.py -v` → 全部 PASS。
全量回归 → 全绿（确认既有 test_station_pages.py / test_station_e2e.py 仍绿；若 test_station_e2e.py 的旧 e2e 测试断言已被 Task 2 重写覆盖，应保持绿）。
```bash
git add src/lightmes/templates/production/station.html src/lightmes/templates/production/partials/station_pass_result.html src/lightmes/modules/production/router.py tests/modules/production/test_station_main_flow.py
git commit -m "feat: station ready page (station select + wo select + scan) + pass-result keeps wo context"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2f spec §3/§4/§5/§6/§7）：
- CarrierService 拆 `bind_first_carrier`（只绑不过站）+ 删 `bind_and_pass_first` → Task 1 ✅
- `POST /production/station/enter`（三路判定 + 防伪造 + DomainError rollback）→ Task 2 ✅
- `GET /production/station/work-orders`（按作业站联动）→ Task 2 ✅
- 删 select-wo / bind-and-pass 路由 + 模板（station_wo_selected.html / station_bind_result.html）→ Task 2 ✅
- 就绪页三栏（作业站下拉+工单下拉+扫码）+ 联动 JS → Task 3 ✅
- station_wo_options.html / station_enter_error.html 新 partials → Task 2 ✅
- station_pass_result.html 保持工单上下文（产品决策默认"继续扫同工单")→ Task 3 ✅
- 富主界面 station_view.html 复用不改 + pass_operation 不改 + StationService.load 不改 → 全程遵守 ✅
- 端到端测试（选站→选工单→扫载体码→富界面→手动 PASS→重置）+ 只绑不过站断言（无 OperationRecord）→ Task 3 ✅

**占位符扫描**：所有 code step 含完整代码。Task 3 Step 3 的 JS 用了 htmx 全局对象与自定义事件 reset-wo，已在注释中说明机制；Task 3 Step 4 的 wo_id 解析用 `SerialUnitRepository(db).get_by_sn(result.sn)` 防御 None。

**类型一致性**：`bind_first_carrier(work_order_id, carrier_code, operator_id) -> SerialUnit`（Task 1 定义）与 Task 2 enter 路由调用一致；`selectable_for_station(line_id)` / `count_pending_by_work_order(wo_id)` / `get_active_by_carrier(code)` / `get_by_sn(sn)` 与 P2e 已实现签名一致；`StationView`（P2d 已定义）与 station_view.html 渲染字段一致；DomainError→rollback+station_enter_error.html 渲染在 Task 2 实现，与 spec §3 一致 ✅。

**关键回归风险**（已在 Task 1 Step 4 标注）：删 `bind_and_pass_first` 会让既有 `/production/station/bind-and-pass` 路由与 `test_station_carrier_pages.py` 的旧测试失败——这是**预期跨任务状态**，Task 2 会移除该路由与旧测试。Task 1 全量回归会显示这些预期失败，Task 2 完成后全量绿。
