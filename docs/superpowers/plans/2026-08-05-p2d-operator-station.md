# P2d 工位作业主界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建操作员工位作业主界面 `/production/station`——扫码进入富交互界面（工艺路径全景 + 当前工序物料绑定 + 参数手录 + 技能资格状态）→ 一键确认过站 → 重置扫下一单元；复用既有后端能力，不改 pass_operation。

**Architecture:** 读写分离三路由。新增只读 `StationService.load()` 组装 `StationView` 读模型（复用 query_service / skill_service / repository，不写库）；`GET /production/station` 出扫码就绪页，`POST /production/station/load` 出富界面 partial，`POST /production/station/pass` 组 `OperationPassInput` 复用 `pass_operation()`。既有 `/production/scan` 保持不变。SOP/PLC/ANDON/跳站无后端 → 界面留占位/禁用。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`（`uv run`）。测试命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- **无新表 / 无迁移**：SOP 延后，operation.sop_id 保持预留。本期只加 Pydantic 读模型 + service + 路由 + 模板。
- **pass_operation 不改**：三层防跳站 / 乐观锁 / 技能硬校验全部沿用。
- **operator_id 服务端覆盖**：load 与 pass 两路由均取 `current_user.id`，Form 传入的 operator_id 一律忽略（防伪造）。
- **读写分离**：load 只读（NotFoundError/BusinessRuleError → rollback + 红片段，不写库）；pass 写（复用 pass_operation，DomainError → rollback + 红片段）。
- **StationView.load 定位规则**（镜像 pass_operation 的定位）：先按 SN 试（`get_by_sn`），未命中再按工单号试（`get_by_code`，视为首件 su=None，current_operation_seq=0）；都未命中 → NotFoundError。
- **当前工序判定**：`current_seq = su.current_operation_seq if su else 0`；`expected = 第一个 seq > current_seq 的工序`（None=已完工）。op.seq <= current_seq → "done"；op is expected → "current"；其余 → "future"。
- UI：HTMX 服务端渲染 + 薄荷绿卡片（复用 `.card`/`.data-table`/`.alert`/`.badge` 等 app.css 样式）+ station 专属类 `.station-*`；写操作 require_login（页面 `current_user_or_none`→401+HX-Redirect `/login`）；`{{ }}` 自动转义。
- 跨模块只读走 `MasterDataQueryService`；技能查询走 `SkillService.get_operator_level`；用户查询走 `UserRepository.get`。事务边界 get_db；repository 只 flush。
- 提交前缀 `feat:`/`test:`/`chore:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

P2d 结束时新增/修改：

```
src/lightmes/modules/production/
├── schemas.py           # 改：加 StationOpView / StationComponentView / StationView 读模型
├── station_service.py   # 新：StationService.load()（只读组装 StationView）
└── router.py            # 改：加 GET /production/station、POST /production/station/load、POST /production/station/pass
src/lightmes/templates/production/
├── station.html                     # 新：扫码就绪页
├── station_view.html                # 新：富界面 partial（load 渲染）
└── partials/station_pass_result.html# 新：过站成功/失败结果 partial
src/lightmes/templates/home.html     # 改：生产执行卡片加"工位作业"入口
src/lightmes/static/css/app.css      # 改：追加 .station-* + .btn-secondary 样式
tests/modules/production/
├── test_station_service.py          # StationService.load 组装四态 + 工序判定
└── test_station_pages.py            # 三路由页面测试（load/pass/require_login/operator_id覆盖）
```

> StationService 独立文件（不塞进 OperationPassService，读写分离清晰）。

---

### Task 1: StationView 读模型 + StationService.load（只读组装）

**Files:**
- Modify: `src/lightmes/modules/production/schemas.py`（加三个读模型）
- Create: `src/lightmes/modules/production/station_service.py`
- Test: `tests/modules/production/test_station_service.py`

**Interfaces:**
- Consumes:
  - `MasterDataQueryService`: `get_product(id)`, `get_operations(routing_id)`, `get_active_bom_items(product_id)`, `get_work_station(id)`（已存在，见 masterdata/query_service.py）
  - `SkillService.get_operator_level(user_id, skill_id) -> int | None`（P2c，已存在）
  - `SerialUnitRepository.get_by_sn(sn)`, `WorkOrderRepository.get_by_code(code)`, `WorkOrderRepository.get(id)`（已存在，见 production/repository.py）
  - `UserRepository.get(user_id) -> User | None`（P2c 已加）
  - 模型字段：`Operation.seq/name/code/default_work_station_id/required_skill_id/required_level`；`BomItem.component_product_id/qty`；`Product.code/name`；`SerialUnit.current_operation_seq`；`WorkOrder.code/product_id/routing_id`
  - 异常：`NotFoundError`, `BusinessRuleError`（shared/errors.py）
- Produces:
  - `schemas.StationOpView`(seq:int, name:str, code:str, work_station_id:int, status:str)
  - `schemas.StationComponentView`(component_product_id:int, component_code:str, component_name:str, qty:float)
  - `schemas.StationView`(sn, work_order_code, product_code, product_name, operator_name, operator_skill_level:int|None, required_level:int|None, skill_ok:bool, is_off_station:bool, is_finished:bool, operations:list[StationOpView], current_op:StationOpView|None, components:list[StationComponentView], sop_placeholder:bool)
  - `StationService(db)` with `load(scan:str, work_station_id:int, operator_id:int|None) -> StationView`

- [ ] **Step 1: 加读模型 schema**

在 `src/lightmes/modules/production/schemas.py` 末尾加：
```python
class StationOpView(BaseModel):
    seq: int
    name: str
    code: str
    work_station_id: int
    status: str  # "done" | "current" | "future"


class StationComponentView(BaseModel):
    component_product_id: int
    component_code: str
    component_name: str
    qty: float


class StationView(BaseModel):
    sn: str
    work_order_code: str
    product_code: str
    product_name: str
    operator_name: str
    operator_skill_level: int | None
    required_level: int | None
    skill_ok: bool
    is_off_station: bool
    is_finished: bool
    operations: list[StationOpView]
    current_op: StationOpView | None
    components: list[StationComponentView]
    sop_placeholder: bool = True
```

- [ ] **Step 2: 写失败测试**

`tests/modules/production/test_station_service.py`（参考 tests/modules/production/test_operation_pass_skill.py 的 fixture 构建方式）:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.station_service import StationService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError


def _setup(db_session, required_skill=False, op_level=None, operator_level=None,
           with_bom=False):
    md = MasterDataService(db_session)
    sk = SkillService(db_session)
    user = User(username="stop", password_hash="x", display_name="工人张")
    db_session.add(user); db_session.flush()
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="P", name="成品", type="finished"))
    comp = md.create_product(ProductCreate(code="C1", name="组件1", type="component"))
    skill = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    ops = [
        OperationCreate(seq=10, code="OP10", name="工序10", default_work_station_id=ws1.id),
        OperationCreate(seq=20, code="OP20", name="工序20", default_work_station_id=ws2.id),
    ]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    if required_skill:
        op0 = md.routings.operations_of(routing.id)[0]
        op0.required_skill_id = skill.id
        op0.required_level = op_level
        db_session.flush()
    if operator_level is not None:
        sk.set_operator_skill(user.id, skill.id, operator_level)
    if with_bom:
        bom = md.create_bom(BomCreate(product_id=p.id, items=[
            BomItemCreate(component_product_id=comp.id, qty=2)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return db_session, ws1, ws2, user, comp


def test_load_first_item_by_work_order(db_session):
    db, ws1, ws2, user, comp = _setup(db_session)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.work_order_code == "WO"
    assert view.product_name == "成品"
    assert view.operator_name == "工人张"
    # 首件 current_seq=0 → 第一道工序为 current，其余 future
    assert [o.status for o in view.operations] == ["current", "future"]
    assert view.current_op.seq == 10
    assert view.is_finished is False


def test_load_no_skill_requirement_ok(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, required_skill=False)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.required_level is None
    assert view.skill_ok is True
    assert view.operator_skill_level is None


def test_load_skill_insufficient_flags_not_ok(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, required_skill=True, op_level=3, operator_level=1)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.required_level == 3
    assert view.operator_skill_level == 1
    assert view.skill_ok is False  # 1 < 3


def test_load_skill_sufficient_ok(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, required_skill=True, op_level=2, operator_level=3)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert view.skill_ok is True  # 3 >= 2


def test_load_off_station_flagged(db_session):
    # 当前工序在 ws1，但用 ws2 加载 → is_off_station True
    db, ws1, ws2, user, comp = _setup(db_session)
    view = StationService(db).load("WO", ws2.id, user.id)
    assert view.is_off_station is True


def test_load_components_from_active_bom(db_session):
    db, ws1, ws2, user, comp = _setup(db_session, with_bom=True)
    view = StationService(db).load("WO", ws1.id, user.id)
    assert len(view.components) == 1
    assert view.components[0].component_code == "C1"
    assert view.components[0].qty == 2


def test_load_unknown_scan_raises_not_found(db_session):
    db, ws1, ws2, user, comp = _setup(db_session)
    with pytest.raises(NotFoundError):
        StationService(db).load("NOPE", ws1.id, user.id)
```
> 若 `md.create_bom` / `BomCreate` / `BomItemCreate` 签名不同，grep `class BomCreate` in masterdata/schemas.py 与 `def create_bom` in masterdata/service.py 校正（P2a/P2b 已建）。`md.routings.operations_of` 已存在（query_service 用同名）。

- [ ] **Step 3: 运行确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_service.py -v`
Expected: FAIL（ModuleNotFoundError: station_service）。

- [ ] **Step 4: 写 StationService**

`src/lightmes/modules/production/station_service.py`:
```python
from sqlalchemy.orm import Session

from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.production.repository import (
    SerialUnitRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    StationOpView, StationComponentView, StationView,
)
from lightmes.shared.errors import NotFoundError, BusinessRuleError


class StationService:
    """只读：扫码组装工位作业主界面读模型，不写库。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.skills = SkillService(db)
        self.users = UserRepository(db)
        self.serial_units = SerialUnitRepository(db)
        self.work_orders = WorkOrderRepository(db)

    def load(self, scan: str, work_station_id: int,
             operator_id: int | None) -> StationView:
        # 定位：先按 SN，再按工单号（首件 su=None）
        su = self.serial_units.get_by_sn(scan)
        if su is not None:
            wo = self.work_orders.get(su.work_order_id)
        else:
            wo = self.work_orders.get_by_code(scan)
            if wo is None:
                raise NotFoundError(f"未找到 SN 或工单: {scan}")

        product = self.query.get_product(wo.product_id)
        operations = self.query.get_operations(wo.routing_id)
        if not operations:
            raise BusinessRuleError("工艺路径无工序")

        current_seq = su.current_operation_seq if su is not None else 0
        expected = next((o for o in operations if o.seq > current_seq), None)

        op_views: list[StationOpView] = []
        for o in operations:
            if o.seq <= current_seq:
                st = "done"
            elif expected is not None and o.id == expected.id:
                st = "current"
            else:
                st = "future"
            op_views.append(StationOpView(
                seq=o.seq, name=o.name, code=o.code,
                work_station_id=o.default_work_station_id, status=st))

        current_op = next((v for v in op_views if v.status == "current"), None)

        # 技能预判
        operator_skill_level: int | None = None
        required_level: int | None = None
        skill_ok = True
        is_off_station = False
        components: list[StationComponentView] = []
        if expected is not None:
            is_off_station = expected.default_work_station_id != work_station_id
            if expected.required_skill_id is not None:
                required_level = expected.required_level
                operator_skill_level = (
                    self.skills.get_operator_level(
                        operator_id, expected.required_skill_id)
                    if operator_id else None)
                skill_ok = (operator_skill_level is not None
                            and operator_skill_level >= (required_level or 0))
            for item in self.query.get_active_bom_items(product.id):
                comp = self.query.get_product(item.component_product_id)
                components.append(StationComponentView(
                    component_product_id=item.component_product_id,
                    component_code=comp.code if comp else str(item.component_product_id),
                    component_name=comp.name if comp else "",
                    qty=float(item.qty)))

        operator = self.users.get(operator_id) if operator_id else None
        return StationView(
            sn=su.sn if su is not None else "",
            work_order_code=wo.code,
            product_code=product.code if product else "",
            product_name=product.name if product else "",
            operator_name=operator.display_name if operator else "",
            operator_skill_level=operator_skill_level,
            required_level=required_level,
            skill_ok=skill_ok,
            is_off_station=is_off_station,
            is_finished=expected is None,
            operations=op_views,
            current_op=current_op,
            components=components,
            sop_placeholder=True,
        )
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_service.py -v` → PASS（7）。
全量回归：`DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest` → 全绿。
```bash
git add src/lightmes/modules/production/schemas.py src/lightmes/modules/production/station_service.py tests/modules/production/test_station_service.py
git commit -m "feat: add StationService.load and StationView read-model"
```

---

### Task 2: 三路由（GET station / POST load / POST pass）

**Files:**
- Modify: `src/lightmes/modules/production/router.py`（加三个处理器 + import）
- Test: `tests/modules/production/test_station_pages.py`

**Interfaces:**
- Consumes:
  - `StationService.load(scan, work_station_id, operator_id) -> StationView`（Task 1）
  - `OperationPassService.pass_operation(OperationPassInput) -> OperationPassResult`（已存在，不改）
  - `OperationPassInput`, `ComponentInput`, `ParamInput`（production/schemas.py，已存在）
  - `current_user_or_none(request, db) -> User | None`（auth/dependencies.py，已存在）
  - 异常：`DomainError`, `NotFoundError`（shared/errors.py）
- Produces:
  - `GET /production/station?work_station_id=<int>` → 渲染 `production/station.html`
  - `POST /production/station/load`（Form: work_station_id, scan）→ 渲染 `production/station_view.html`（StationView），错误→红片段
  - `POST /production/station/pass`（Form: work_station_id, scan, component_product_id[], component_batch[], param_key[], param_value[], param_unit[]）→ 渲染 `production/partials/station_pass_result.html`

- [ ] **Step 1: 写失败测试**

`tests/modules/production/test_station_pages.py`:
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
    AuthService(db_session).create_user(UserCreate(username="st", password="pw12345", display_name="St"))
    db_session.flush()
    client.post("/login", data={"username": "st", "password": "pw12345"})


def _prod(db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="成品", type="finished"))
    ops = [OperationCreate(seq=10, code="OP10", name="工序10", default_work_station_id=ws.id)]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    return ws


def test_station_page_renders(client, db_session):
    ws = _prod(db_session)
    resp = client.get(f"/production/station?work_station_id={ws.id}")
    assert resp.status_code == 200
    assert "工位作业" in resp.text


def test_load_renders_rich_view(client, db_session):
    ws = _prod(db_session)
    _login(client, db_session)
    resp = client.post("/production/station/load",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200
    assert "工序10" in resp.text  # 路径全景含工序名


def test_load_unknown_scan_shows_error(client, db_session):
    ws = _prod(db_session)
    _login(client, db_session)
    resp = client.post("/production/station/load",
                       data={"work_station_id": str(ws.id), "scan": "NOPE"})
    assert resp.status_code == 200
    assert "未找到" in resp.text


def test_load_requires_login(client, db_session):
    ws = _prod(db_session)
    resp = client.post("/production/station/load",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 401


def test_pass_first_item_success(client, db_session):
    ws = _prod(db_session)
    _login(client, db_session)
    resp = client.post("/production/station/pass",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 200
    assert "已过" in resp.text or "完工" in resp.text


def test_pass_requires_login(client, db_session):
    ws = _prod(db_session)
    resp = client.post("/production/station/pass",
                       data={"work_station_id": str(ws.id), "scan": "WO"})
    assert resp.status_code == 401
```

- [ ] **Step 2: 运行确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pages.py -v`
Expected: FAIL（404/500，路由未建 + 模板缺失）。模板将在 Task 3 建；本 Task 先建路由，Task 3 建模板后测试才全绿——**故本 Task 的 Step 1 测试会依赖 Task 3 模板**。为让本 Task 可独立通过，Step 3 里同时建**最小占位模板**（Task 3 再替换为完整 UI）。

- [ ] **Step 3: 写路由 + 最小占位模板**

在 `src/lightmes/modules/production/router.py` import 区加：
```python
from lightmes.modules.production.schemas import (
    SnRuleCreate, SnRuleRead, OperationPassInput, OperationPassResult, WorkOrderCreate,
    WorkOrderRead, ComponentInput, ParamInput,
)
from lightmes.modules.production.station_service import StationService
```
（把 `ComponentInput, ParamInput` 并入现有 schemas import；`StationService` 新增一行。）

在文件末尾加三个处理器：
```python
@router.get("/production/station", response_class=HTMLResponse)
def station_page(
    request: Request, work_station_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "production/station.html", {"work_station_id": work_station_id}
    )


@router.post("/production/station/load", response_class=HTMLResponse)
def station_load(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        view = StationService(db).load(scan, work_station_id, user.id)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_pass_result.html", {"error": e.detail}
        )
    return templates.TemplateResponse(
        request, "production/station_view.html",
        {"view": view, "work_station_id": work_station_id},
    )


@router.post("/production/station/pass", response_class=HTMLResponse)
def station_pass(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
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
    # 组件：仅收已扫批次的行
    components = [
        ComponentInput(component_product_id=pid, component_batch_no=batch.strip())
        for pid, batch in zip(component_product_id, component_batch)
        if batch.strip()
    ]
    # 参数：仅收 key+value 都非空的行
    params = []
    for i, key in enumerate(param_key):
        if not key.strip():
            continue
        val = param_value[i] if i < len(param_value) else ""
        if not val.strip():
            continue
        unit = param_unit[i].strip() if i < len(param_unit) and param_unit[i].strip() else None
        params.append(ParamInput(param_key=key.strip(), param_value=val.strip(), unit=unit))

    svc = OperationPassService(db)
    data = OperationPassInput(
        work_station_id=work_station_id, operator_id=user.id,
        components=components, params=params)
    # 先按 SN 试，NotFound 再当工单号（首件）
    try:
        data.sn = scan
        try:
            result = svc.pass_operation(data)
        except NotFoundError:
            data.sn = None
            data.work_order_code = scan
            result = svc.pass_operation(data)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_pass_result.html", {"error": e.detail}
        )
    return templates.TemplateResponse(
        request, "production/partials/station_pass_result.html",
        {"result": result, "work_station_id": work_station_id},
    )
```
建**最小占位模板**（Task 3 会替换为完整 UI）：

`src/lightmes/templates/production/station.html`:
```html
{% extends "base.html" %}
{% block title %}工位作业{% endblock %}
{% block content %}
<h1 class="page-title">工位作业 <small>作业站 #{{ work_station_id }}</small></h1>
<div id="station-root"></div>
{% endblock %}
```
`src/lightmes/templates/production/station_view.html`:
```html
<div>{% for o in view.operations %}<span>{{ o.seq }} {{ o.name }} [{{ o.status }}]</span>{% endfor %}</div>
```
`src/lightmes/templates/production/partials/station_pass_result.html`:
```html
{% if error %}
<div class="alert alert--danger">✗ {{ error }}</div>
{% else %}
<div class="alert alert--ok">✓ <strong>{{ result.sn }}</strong> — 已过 工序{{ result.passed_op.seq }} {{ result.passed_op.name }}
{% if result.is_finished %}<span class="badge">完工</span>{% else %} → 下一站：工序{{ result.next_op.seq }} {{ result.next_op.name }}{% endif %}</div>
{% endif %}
```

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pages.py -v` → PASS（6）。
全量回归 → 全绿（既有 /production/scan 测试不受影响）。
```bash
git add src/lightmes/modules/production/router.py src/lightmes/templates/production/station.html src/lightmes/templates/production/station_view.html src/lightmes/templates/production/partials/station_pass_result.html tests/modules/production/test_station_pages.py
git commit -m "feat: add station load/pass routes with placeholder templates"
```

---

### Task 3: 完整 UI（富界面模板 + app.css station 样式 + 首页导航）

**Files:**
- Modify: `src/lightmes/templates/production/station.html`（完整就绪页）
- Modify: `src/lightmes/templates/production/station_view.html`（完整富界面）
- Modify: `src/lightmes/templates/production/partials/station_pass_result.html`（含重置提示）
- Modify: `src/lightmes/static/css/app.css`（追加 `.station-*` + `.btn-secondary`）
- Modify: `src/lightmes/templates/home.html`（生产执行卡片加入口）
- Test: 复用 Task 2 的 `tests/modules/production/test_station_pages.py`，加两条断言（技能徽章 + 物料表）

**Interfaces:**
- Consumes: `StationView`（Task 1）字段；HTMX（base.html 已引入本地 htmx）。
- Produces: 完整工位主界面（顶部状态栏 / 工艺路径全景 / 物料绑定 / 参数手录 / SOP 占位 / PASS 底栏）。

- [ ] **Step 1: 加断言到现有测试**

在 `tests/modules/production/test_station_pages.py` 的 `test_load_renders_rich_view` 末尾追加：
```python
    assert "工人" in resp.text or "操作员" in resp.text  # 顶部操作员区
    assert "确认过站" in resp.text                        # PASS 按钮
```
> 若登录用户 display_name 非"工人"，此断言用"操作员"标签兜底（模板含"操作员"字样）。

- [ ] **Step 2: 运行确认失败**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pages.py::test_load_renders_rich_view -v`
Expected: FAIL（占位模板无"确认过站"）。

- [ ] **Step 3: 写完整模板**

`src/lightmes/templates/production/station.html`（就绪页：扫码 → load）:
```html
{% extends "base.html" %}
{% block title %}工位作业{% endblock %}
{% block content %}
<h1 class="page-title">工位作业主界面 <small>作业站 #{{ work_station_id }}</small></h1>
<div class="card">
  <div class="card__title">扫码进入</div>
  <form class="form-row" hx-post="/production/station/load" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=scan]').value=''">
    <div class="field"><label>作业站</label><input name="work_station_id" value="{{ work_station_id }}"></div>
    <div class="field" style="flex:1"><label>扫码 / 输入</label>
      <input name="scan" placeholder="首件填工单号，后续填 SN" required autofocus></div>
    <button type="submit">加载</button>
  </form>
</div>
<div id="station-root" class="station-root"></div>
{% endblock %}
```

`src/lightmes/templates/production/station_view.html`（富界面）:
```html
<div class="station">
  <!-- 顶部状态栏 -->
  <div class="station__topbar">
    <div class="station__ident">
      <div><span class="station__label">当前 SN</span>
        <span class="station__sn">{{ view.sn or "（首件待生成）" }}</span></div>
      <div><span class="station__label">成品 / 工单</span>
        <span>{{ view.product_code }} {{ view.product_name }} / {{ view.work_order_code }}</span></div>
    </div>
    <div class="station__operator {% if view.skill_ok %}is-ok{% else %}is-bad{% endif %}">
      <span class="station__label">操作员</span>
      <span class="station__opname">{{ view.operator_name }}
        {% if view.required_level %}(L{{ view.operator_skill_level or '无' }}/需L{{ view.required_level }}){% endif %}</span>
    </div>
  </div>

  {% if view.is_off_station %}
  <div class="alert alert--danger">⚠ 当前工序不属于本作业站，过站将被防跳站拦截。</div>
  {% endif %}
  {% if not view.skill_ok %}
  <div class="alert alert--danger">⚠ 操作员技能不足，过站将被拦截。</div>
  {% endif %}

  <!-- 工艺路径全景 -->
  <div class="card">
    <div class="card__title">工艺路径全景</div>
    <div class="station__path" id="station-path">
      {% for o in view.operations %}
      <div class="station__step station__step--{{ o.status }}" {% if o.status == 'current' %}id="station-current"{% endif %}>
        <div class="station__step-node">{% if o.status == 'done' %}✓{% else %}{{ o.seq }}{% endif %}</div>
        <div class="station__step-name">{{ o.name }}</div>
        {% if o.status == 'current' %}<div class="badge">当前</div>{% endif %}
      </div>
      {% endfor %}
    </div>
  </div>

  {% if view.is_finished %}
  <div class="alert alert--ok">该单元已完工，无待执行工序。</div>
  {% else %}
  <!-- PASS 表单：物料 + 参数一起提交 -->
  <form hx-post="/production/station/pass" hx-target="#station-root" hx-swap="innerHTML">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <input type="hidden" name="scan" value="{{ view.sn or view.work_order_code }}">

    <div class="station__grid">
      <div class="station__main">
        <div class="card">
          <div class="card__title">当前工序物料追溯 <span class="badge">BOM 匹配</span></div>
          {% if view.components %}
          <table class="data-table">
            <thead><tr><th>物料</th><th>需求量</th><th>批次/条码（扫码）</th></tr></thead>
            <tbody>
              {% for c in view.components %}
              <tr>
                <td>{{ c.component_code }} {{ c.component_name }}</td>
                <td>{{ c.qty }}</td>
                <td><input type="hidden" name="component_product_id" value="{{ c.component_product_id }}">
                  <input name="component_batch" placeholder="扫描该物料批次..."></td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}<div class="nav-card__desc">当前工序无 BOM 绑定项。</div>{% endif %}
        </div>

        <div class="card">
          <div class="card__title">工艺参数采集 <span class="badge">手录</span></div>
          <table class="data-table">
            <thead><tr><th>参数名</th><th>数值</th><th>单位</th></tr></thead>
            <tbody>
              {% for i in range(3) %}
              <tr>
                <td><input name="param_key" placeholder="参数名"></td>
                <td><input name="param_value" placeholder="数值"></td>
                <td><input name="param_unit" placeholder="单位"></td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          <div class="nav-card__desc">PLC 自动采集：暂未开放（留白）。</div>
        </div>
      </div>

      <div class="station__aside">
        <div class="card">
          <div class="card__title">SOP 作业指导书</div>
          <div class="station__sop">SOP 内容待建设（本期留白）。</div>
        </div>
        <div class="card">
          <div class="card__title">异常干预</div>
          <a class="btn-secondary" href="/trace/rework">申请返工</a>
          <button type="button" class="btn-secondary" disabled title="暂未开放">申请跳站</button>
        </div>
      </div>
    </div>

    <div class="station__footer">
      <span class="station__status">{% if view.skill_ok and not view.is_off_station %}就绪，可过站{% else %}存在拦截风险{% endif %}</span>
      <button type="button" class="btn-secondary" disabled title="暂未开放">异常呼叫 (ANDON)</button>
      <button type="submit" class="station__pass">确认过站 (PASS)</button>
    </div>
  </form>
  {% endif %}
</div>
<script>
  (function () {
    var cur = document.getElementById('station-current');
    if (cur) cur.scrollIntoView({inline: 'center', block: 'nearest'});
  })();
</script>
```

`src/lightmes/templates/production/partials/station_pass_result.html`（含重置扫下一单元）:
```html
{% if error %}
<div class="alert alert--danger">✗ {{ error }}</div>
<div class="card"><div class="nav-card__desc">修正后请重新扫码。返回 <a href="/production/station?work_station_id={{ work_station_id }}">工位作业</a>。</div></div>
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
  <form class="form-row" hx-post="/production/station/load" hx-target="#station-root" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.querySelector('[name=scan]').value=''">
    <input type="hidden" name="work_station_id" value="{{ work_station_id }}">
    <div class="field" style="flex:1"><label>扫下一单元</label>
      <input name="scan" placeholder="工单号 / SN" autofocus></div>
    <button type="submit">加载</button>
  </form>
</div>
{% endif %}
```

在 `src/lightmes/static/css/app.css` 末尾追加（`.alert`/`.badge`/`.form-row`/`.data-table` 已存在复用；`.btn-secondary` 不存在，一并加）：
```css
/* ---- P2d 工位作业主界面 ---- */
.btn-secondary { background: #e7f2ea; color: #2f7d4f; border: 1px solid #b9d8c4;
  padding: 8px 14px; border-radius: 8px; display: inline-block; text-decoration: none; cursor: pointer; }
.btn-secondary:disabled { opacity: .5; cursor: not-allowed; }
.station__topbar { display: flex; justify-content: space-between; align-items: center;
  background: #16321f; color: #eafff2; padding: 12px 18px; border-radius: 10px; margin-bottom: 12px; }
.station__ident { display: flex; gap: 28px; }
.station__label { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #7fcf9f; }
.station__sn { font-size: 20px; font-weight: 700; color: #6ee7a8; }
.station__operator { border: 1px solid #2f7d4f; border-radius: 8px; padding: 6px 12px; }
.station__operator.is-bad { border-color: #d9534f; }
.station__opname { font-weight: 700; }
.station__path { display: flex; gap: 8px; overflow-x: auto; padding: 12px 4px; }
.station__step { min-width: 120px; text-align: center; opacity: .5; }
.station__step--done, .station__step--current { opacity: 1; }
.station__step-node { width: 44px; height: 44px; margin: 0 auto; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; font-weight: 700;
  background: #fff; border: 2px solid #cbd5c0; }
.station__step--done .station__step-node { background: #34c77b; color: #fff; border-color: #34c77b; }
.station__step--current .station__step-node { background: #2f7d4f; color: #fff; border-color: #2f7d4f;
  box-shadow: 0 0 0 4px rgba(47,125,79,.25); }
.station__step-name { font-size: 12px; margin-top: 6px; }
.station__grid { display: grid; grid-template-columns: 7fr 5fr; gap: 12px; }
.station__main, .station__aside { display: flex; flex-direction: column; gap: 12px; }
.station__sop { min-height: 120px; display: flex; align-items: center; justify-content: center; color: #6b8a76; }
.station__footer { display: flex; align-items: center; gap: 14px; justify-content: flex-end;
  padding: 14px 4px; border-top: 1px solid #d7e6da; margin-top: 12px; }
.station__status { margin-right: auto; font-weight: 600; color: #2f7d4f; }
.station__pass { font-size: 18px; font-weight: 800; padding: 12px 40px; }
@media (max-width: 900px) { .station__grid { grid-template-columns: 1fr; } }
```

在 `src/lightmes/templates/home.html` 的"生产执行"卡片 nav-grid 内（`/production/scan` 卡片之后）加：
```html
    <a class="nav-card" href="/production/station?work_station_id=0">
      <span class="nav-card__icon">🖥️</span>
      <div class="nav-card__name">工位作业</div>
      <div class="nav-card__desc">工位主界面 · 路径/物料/参数/过站</div>
    </a>
```

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_pages.py -v` → PASS。
全量回归 → 全绿。
```bash
git add src/lightmes/templates/production src/lightmes/static/css/app.css src/lightmes/templates/home.html tests/modules/production/test_station_pages.py
git commit -m "feat: full operator station UI (path panorama, materials, params, PASS)"
```

---

### Task 4: 端到端过站链路测试（收尾复审）

**Files:**
- Test: `tests/modules/production/test_station_e2e.py`

**Interfaces:**
- Consumes: 三路由（Task 2/3）；`pass_operation` 复用链路（技能硬拦 / 防跳站 / 乐观锁均已在 pass_operation 内，本 Task 只验端到端表现）。

- [ ] **Step 1: 写端到端测试**

`tests/modules/production/test_station_e2e.py`:
```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session, uname="e2e"):
    AuthService(db_session).create_user(UserCreate(username=uname, password="pw12345", display_name="E2E工"))
    db_session.flush()
    client.post("/login", data={"username": uname, "password": "pw12345"})


def _two_station(db_session, required_skill=False, op_level=None):
    md = MasterDataService(db_session); sk = SkillService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="P", name="成品", type="finished"))
    skill = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    ops = [
        OperationCreate(seq=10, code="OP10", name="工序10", default_work_station_id=ws1.id),
        OperationCreate(seq=20, code="OP20", name="工序20", default_work_station_id=ws2.id),
    ]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    if required_skill:
        op0 = md.routings.operations_of(routing.id)[0]
        op0.required_skill_id = skill.id; op0.required_level = op_level
        db_session.flush()
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id); db_session.flush()
    return ws1, ws2, skill


def test_e2e_scan_load_pass_reset(client, db_session):
    ws1, ws2, skill = _two_station(db_session)
    _login(client, db_session)
    # 首件加载
    r1 = client.post("/production/station/load", data={"work_station_id": str(ws1.id), "scan": "WO"})
    assert r1.status_code == 200 and "工序10" in r1.text
    # 过首站 → 成功 + 出现"扫下一单元"
    r2 = client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "WO"})
    assert r2.status_code == 200 and "已过" in r2.text and "下一单元" in r2.text


def test_e2e_off_station_blocked(client, db_session):
    ws1, ws2, skill = _two_station(db_session)
    _login(client, db_session)
    client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "WO"})
    # 首件已在 ws1 过了工序10，SN=SN00001；用 ws1 再过 → 应到工序20@ws2，防跳站拦
    r = client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "SN00001"})
    assert r.status_code == 200 and "✗" in r.text


def test_e2e_skill_insufficient_blocked(client, db_session):
    ws1, ws2, skill = _two_station(db_session, required_skill=True, op_level=3)
    _login(client, db_session)  # 登录用户无技能档案
    r = client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "WO"})
    assert r.status_code == 200 and "✗" in r.text and "技能" in r.text


def test_e2e_operator_id_cannot_be_spoofed(client, db_session):
    ws1, ws2, skill = _two_station(db_session, required_skill=True, op_level=1)
    # 登录用户有技能，但表单传假 operator_id 也应被 current_user 覆盖 → 用真身校验
    _login(client, db_session)
    from lightmes.modules.auth.repository import UserRepository
    uid = UserRepository(db_session).get_by_username("e2e").id
    SkillService(db_session).set_operator_skill(uid, skill.id, 2)
    db_session.flush()
    r = client.post("/production/station/pass",
                    data={"work_station_id": str(ws1.id), "scan": "WO", "operator_id": "99999"})
    assert r.status_code == 200 and "已过" in r.text  # 用真身(有技能)过站成功
```
> `test_e2e_off_station_blocked` 依赖首件 SN 形如 `SN00001`（pattern `SN{SEQ:5}`）。若 SN 生成格式不同，先跑一次打印 r2 文本取实际 SN 或改用 load 返回的 SN。稳妥做法：从 r2.text 里解析不便时，改为 `su = SerialUnitRepository(db_session).list_by_work_order(wo.id)[0]` 取 sn——此时需把 wo 传出。MVP 先按 `SN00001` 断言，失败则改解析。

- [ ] **Step 2: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_e2e.py -v` → PASS（4）。
全量回归 → 全绿。
```bash
git add tests/modules/production/test_station_e2e.py
git commit -m "test: end-to-end station scan/load/pass/reset + block coverage"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2d spec §3/§4/§5/§7/§8）：
- StationView 读模型 + StationService.load（只读组装、当前工序判定、skill 预判、is_off_station、components）→ Task 1 ✅
- 三路由（GET station / POST load / POST pass，读写分离、require_login、operator_id 服务端覆盖、DomainError rollback）→ Task 2 ✅
- 完整 UI（顶部状态栏 / 路径全景自动居中 / 物料绑定 / 参数手录 / SOP 占位 / 异常干预禁用 / PASS 底栏）+ app.css + 首页导航 → Task 3 ✅
- PASS 后重置扫下一单元 → Task 3 station_pass_result.html ✅
- 端到端 + 技能拦 / 防跳站拦 / operator_id 防伪造 → Task 4 ✅
- 无后端项留占位/禁用（PLC/SOP/ANDON/跳站）→ Task 3 模板 ✅
- 无新表 / 不改 pass_operation → 全程遵守 ✅

**占位符扫描**：所有 code step 均含完整代码。Task 2 的富 UI 用最小占位模板过测、Task 3 替换为完整版——这是有意的任务边界（路由与 UI 分开评审），非占位符缺失。app.css 路径为 `src/lightmes/static/css/app.css`（已核实），`.alert`/`.badge`/`.form-row`/`.data-table` 已存在复用，`.btn-secondary` 不存在故 Task 3 一并新增。

**类型一致性**：`StationOpView`/`StationComponentView`/`StationView` 字段（Task 1 定义）与模板引用（Task 3）、service 组装（Task 1）一致；`StationService.load(scan, work_station_id, operator_id)` 签名定义处（Task 1）与调用处（Task 2 router）一致；`ComponentInput(component_product_id, component_batch_no)` / `ParamInput(param_key, param_value, unit)` 与既有 schemas 一致；`current_user_or_none`/`DomainError`/`NotFoundError` 与既有 router import 一致 ✅。

**依赖校验**：`UserRepository.get`（P2c Task 2 已加，skill_service 依赖它）；`md.routings.operations_of`（query_service 已用同名）；`get_active_bom_items`（query_service 已存在）；`create_bom/BomCreate/BomItemCreate`（P2b 已建，Task 1 测试若签名不符按注释 grep 校正）。
