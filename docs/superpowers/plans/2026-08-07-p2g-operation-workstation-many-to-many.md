# P2g 工序-作业站多对多 + 连续过站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工序↔作业站从单值 FK（default_work_station_id）改成两层模型——保留默认站 + 新增 operation_work_stations 多对多关联表；过站判定从"等于默认"改为"属于允许集合"；同一作业站上 PASS 成功后若下一工序也允许在本站做，则直接刷新富界面到下一工序（不回扫码页）。

**Architecture:** 新建 `operation_work_stations` 关联表 + 数据迁移（每个现有 operation 插一条默认站记录）；`OperationCreate` 加 `allowed_work_station_ids: list[int]`；`MasterDataService.create_routing` 校验 default ∈ allowed 并写关联表；`MasterDataQueryService.get_allowed_work_stations(op_id)` 只读查询；`pass_operation` 防跳站第二层 + `StationService.load` off-station 判定改为 `work_station_id in allowed`；`OperationPassResult.next_op_can_continue_here` 新字段；`station_pass` 成功分流（finished / continue-here-render-station_view / switch-station-prompt）；路径全景每个工序节点显示完整 allowed 站名列表；主数据维护 UI 工序表单加 allowed 多选（带产线名标签）+ 默认站联动。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Jinja2 + HTMX（本地托管，无 CDN）, PostgreSQL, pytest, uv。

## Global Constraints

- Python 3.12；依赖 `uv`。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0 风格（`Mapped[]`/`mapped_column`，继承 Base+TimestampMixin）；Alembic 迁移；autogenerate 后**打开迁移确认只动预期表/索引**，不得误删既有索引（uq_active_*/uq_operation_*/uq_*_erp_ref/uq_bom_item_component/uq_operator_skill_user_skill）。
- **关联表 `operation_work_stations`**：`id` PK，`operation_id` FK→operations（ON DELETE CASCADE），`work_station_id` FK→work_stations，唯一约束 `(operation_id, work_station_id)`。TimestampMixin。
- **保留 `operation.default_work_station_id`**（NOT NULL FK），不删列。`default ∈ allowed`（service 层校验）。
- **数据迁移**：对每个现有 operation 插一条 `(operation_id, default_work_station_id)` 到关联表，让现状数据天然满足"默认站也在允许集合"。
- **过站判定**：防跳站第二层从 `ws_id == default_work_station_id` 改为 `ws_id in allowed_ids`；第一层（`ws.line_id != wo.line_id`）不变；第三层（pending/技能/乐观锁/完工）不变。
- **连续过站**：`station_pass` 成功分流——`is_finished` 渲染完工片段；`next_op_can_continue_here` 调 StationService.load 刷富界面；否则切站提示。
- **`OperationPassResult.next_op_can_continue_here: bool = False`**：next_op 非空且 next_op 的 allowed 含当前 work_station_id。
- **`StationOpView.allowed_work_stations: list[str]`**（作业站名列表）：路径全景每个工序节点显示完整 allowed 站名。
- **operator_id 服务端赋值**（防伪造）；写操作 require_login；DomainError → `db.rollback()` + 错误片段。NO CDN；Jinja2 `{{ }}` 自动转义。
- 提交前缀 `feat:`/`refactor:`/`test:`；每 Task 末尾提交。DRY/YAGNI/TDD。DB 需 running。

---

## File Structure

P2g 结束时新增/修改：

```
src/lightmes/modules/masterdata/
├── models.py             # 改：加 OperationWorkStation 关联表模型
├── schemas.py            # 改：OperationCreate 加 allowed_work_station_ids；OperationRead 加 allowed_work_station_ids
├── service.py            # 改：create_routing 校验 default ∈ allowed + 写关联表
├── repository.py         # 改：加 OperationWorkStationRepository
├── query_service.py      # 改：加 get_allowed_work_stations(op_id)
└── router.py             # 改：工序表单路由接收 allowed_work_station_ids 多值；operation_read 序列化 allowed
src/lightmes/modules/production/
├── operation_pass_service.py  # 改：防跳站第二层 + next_op_can_continue_here 计算
├── station_service.py         # 改：off-station 判定 + StationOpView.allowed_work_stations 填充
├── schemas.py                 # 改：OperationPassResult 加 next_op_can_continue_here；StationOpView 加 allowed_work_stations
└── router.py                  # 改：station_pass 成功分流（finished / continue-here / switch-station-prompt）
src/lightmes/migrations/versions/  # 新：operation_work_stations 表 + 数据迁移
src/lightmes/templates/production/
└── partials/station_pass_result.html  # 改：加切站提示分支
src/lightmes/templates/masterdata/routings.html  # 改（或其 partials）：工序表单加 allowed 多选 + 默认站联动
tests/modules/masterdata/  # 关联表写入 + default ∈ allowed 校验 + get_allowed_work_stations
tests/modules/production/  # 防跳站第二层 + off-station + next_op_can_continue_here + 连续过站端到端
```

---

### Task 1: 数据模型 + 迁移 + create_routing 写关联表

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`（加 OperationWorkStation）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（OperationCreate/OperationRead 加 allowed_work_station_ids）
- Modify: `src/lightmes/modules/masterdata/service.py`（create_routing 校验 + 写关联表）
- Modify: `src/lightmes/modules/masterdata/repository.py`（加 OperationWorkStationRepository）
- Create: `src/lightmes/migrations/versions/<auto>_add_operation_work_stations.py`
- Test: `tests/modules/masterdata/test_operation_work_station.py`

**Interfaces:**
- Produces:
  - `OperationWorkStation` model (operation_id FK CASCADE, work_station_id FK, UniqueConstraint)
  - `OperationCreate.allowed_work_station_ids: list[int]`
  - `OperationRead.allowed_work_station_ids: list[int]`
  - `OperationWorkStationRepository.add(op_id, ws_id)` / `list_by_operation(op_id) -> list[OperationWorkStation]`
  - `MasterDataService.create_routing` 写关联表 + 校验 default ∈ allowed
  - 迁移：create_table + 数据迁移（每个现有 operation 插一条默认站）

- [ ] **Step 1: 加 OperationWorkStation 模型**

在 `src/lightmes/modules/masterdata/models.py` 顶部 import 加 `UniqueConstraint`（若未 import）。在 Operation 类之后加：
```python
class OperationWorkStation(Base, TimestampMixin):
    __tablename__ = "operation_work_stations"
    __table_args__ = (
        UniqueConstraint("operation_id", "work_station_id",
                         name="uq_operation_work_station"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
```
确认顶部 import 行含 `ForeignKey, UniqueConstraint`（现状已有 `ForeignKey, Index, JSON, Numeric, text, UniqueConstraint`）。

- [ ] **Step 2: 改 schemas.py**

在 `src/lightmes/modules/masterdata/schemas.py` 的 `OperationCreate` 加字段：
```python
class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int]  # 新增：至少 1 个；必须含 default_work_station_id
    is_mandatory: bool = True
    required_skill_id: int | None = None
    required_level: int | None = None
```
`OperationRead` 也加（用于序列化展示）：
```python
class OperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    routing_id: int
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int] = []
    is_mandatory: bool
```

- [ ] **Step 3: 加 OperationWorkStationRepository**

在 `src/lightmes/modules/masterdata/repository.py` 末尾加（确认 import 有 `select`）：
```python
class OperationWorkStationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, op_id: int, ws_id: int) -> OperationWorkStation:
        row = OperationWorkStation(operation_id=op_id, work_station_id=ws_id)
        self.db.add(row); self.db.flush(); return row

    def list_by_operation(self, op_id: int) -> list[OperationWorkStation]:
        return list(self.db.execute(
            select(OperationWorkStation)
            .where(OperationWorkStation.operation_id == op_id)
            .order_by(OperationWorkStation.id)
        ).scalars().all())
```
顶部 import 加 `OperationWorkStation` 到现有 `from lightmes.modules.masterdata.models import (...)`。

- [ ] **Step 4: 改 create_routing 写关联表**

`MasterDataService.__init__` 加 `self.op_work_stations = OperationWorkStationRepository(db)`。改 `create_routing` 的工序循环（第 62-93 行）：
```python
        for op in data.operations:
            if self.work_stations.get(op.default_work_station_id) is None:
                raise ValueError(f"作业站不存在: {op.default_work_station_id}")
            # 多对多校验：allowed 非空 + default ∈ allowed + 每个 ws 存在
            if not op.allowed_work_station_ids:
                raise ValueError(f"工序 {op.seq} 必须至少指定一个允许作业站")
            if op.default_work_station_id not in op.allowed_work_station_ids:
                raise ValueError(
                    f"工序 {op.seq} 默认作业站必须在允许作业站列表内")
            for ws_id in op.allowed_work_station_ids:
                if self.work_stations.get(ws_id) is None:
                    raise ValueError(f"作业站不存在: {ws_id}")
            if op.required_skill_id is not None:
                skill = self.skills.get(op.required_skill_id)
                if skill is None:
                    raise ValueError(f"技能不存在: {op.required_skill_id}")
                if op.required_level is None or op.required_level < 1:
                    raise ValueError(f"工序 {op.seq} 设置了技能要求，必须填写要求等级(>=1)")
                if op.required_level > skill.max_level:
                    raise ValueError(
                        f"工序 {op.seq} 要求等级 L{op.required_level} 超过技能『{skill.name}』最高等级 L{skill.max_level}")
        has_active = self.routings.get_active_by_product(data.product_id) is not None
        routing = Routing(
            code=data.code, name=data.name, product_id=data.product_id,
            version=data.version, status="inactive" if has_active else "active",
        )
        self.routings.add(routing)
        for op in data.operations:
            operation = Operation(
                routing_id=routing.id, seq=op.seq, code=op.code, name=op.name,
                default_work_station_id=op.default_work_station_id,
                is_mandatory=op.is_mandatory,
                required_skill_id=op.required_skill_id,
                required_level=op.required_level,
            )
            self.db.add(operation); self.db.flush()  # 拿到 operation.id
            for ws_id in op.allowed_work_station_ids:
                self.op_work_stations.add(operation.id, ws_id)
        self.db.flush()
        return routing
```

- [ ] **Step 5: 写失败测试**

`tests/modules/masterdata/test_operation_work_station.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.repository import (
    OperationWorkStationRepository, WorkStationRepository,
)


def _setup(db_session, allowed_ids):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    wss = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(len(allowed_ids))]
    return md, p, wss


def test_create_routing_writes_allowed(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0, 1])
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序",
                        default_work_station_id=wss[0].id,
                        allowed_work_station_ids=[wss[0].id, wss[1].id])]))
    db_session.flush()
    ops = md.routings.operations_of(routing.id)
    allowed = OperationWorkStationRepository(db_session).list_by_operation(ops[0].id)
    assert {a.work_station_id for a in allowed} == {wss[0].id, wss[1].id}


def test_default_must_be_in_allowed(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0, 1])
    with pytest.raises(ValueError, match="默认作业站必须在允许"):
        md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序",
                            default_work_station_id=wss[0].id,
                            allowed_work_station_ids=[wss[1].id])]))  # default wss[0] 不在 allowed


def test_allowed_cannot_be_empty(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0])
    with pytest.raises(ValueError, match="至少指定一个允许作业站"):
        md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序",
                            default_work_station_id=wss[0].id,
                            allowed_work_station_ids=[])]))


def test_allowed_ws_must_exist(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0])
    with pytest.raises(ValueError, match="作业站不存在"):
        md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
            OperationCreate(seq=10, code="OP10", name="工序",
                            default_work_station_id=wss[0].id,
                            allowed_work_station_ids=[wss[0].id, 999999])]))
```

- [ ] **Step 6: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "add operation_work_stations"
```
**打开迁移文件**确认只 create_table operation_work_stations（含 FK + uq_operation_work_station 唯一约束），不删任何既有索引。`down_revision` 是当前 head（`776a57eedc0f` → `43985d47f2a9` 链，确认最新 head）。

**手工加数据迁移**到 `upgrade()` 末尾（在 create_table 之后）：
```python
    # 数据迁移：每个现有 operation 插一条 (operation_id, default_work_station_id)
    op.execute(sa.text("""
        INSERT INTO operation_work_stations (operation_id, work_station_id, created_at, updated_at)
        SELECT id, default_work_station_id, now(), now() FROM operations
    """))
```
（关联表有 TimestampMixin 的 created_at/updated_at NOT NULL，需填默认）

应用迁移：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
预期成功，且 `SELECT count(*) FROM operation_work_stations` 应等于 `SELECT count(*) FROM operations`。

- [ ] **Step 7: 运行测试 + 回归 + Commit**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_operation_work_station.py -v
```
预期 4 PASS。

**关键回归警告**：现有所有 `RoutingCreate(operations=[OperationCreate(...)])` 调用方（测试 + 主数据 UI 路由）都没传 `allowed_work_station_ids`——Pydantic 会报 ValidationError。**这是预期的跨任务状态**：Task 2 会改主数据 UI 路由接收 allowed；现有测试需要在 Task 1 顺带更新（给每个 OperationCreate 加 `allowed_work_station_ids=[default_work_station_id]` 单元素列表，保持现状行为）。

跑全量回归，列出受影响测试（凡是 RoutingCreate/OperationCreate 的调用），逐个加 `allowed_work_station_ids=[op.default_work_station_id]` 让现状行为不变（单站等于多对多只含默认）。

```bash
git add src/lightmes/modules/masterdata/models.py src/lightmes/modules/masterdata/schemas.py src/lightmes/modules/masterdata/service.py src/lightmes/modules/masterdata/repository.py src/lightmes/migrations tests/
git commit -m "feat: operation_work_stations many-to-many + create_routing writes allowed set"
```

---

### Task 2: query_service.get_allowed_work_stations + 主数据 UI allowed 多选

**Files:**
- Modify: `src/lightmes/modules/masterdata/query_service.py`（加 get_allowed_work_stations）
- Modify: `src/lightmes/modules/masterdata/router.py`（工序表单路由接收 allowed_work_station_ids 多值 + 序列化 allowed）
- Modify: `src/lightmes/templates/masterdata/routings.html`（或其 partials：工序表单加 allowed 多选 + 默认站联动 JS）
- Test: `tests/modules/masterdata/test_operation_work_station.py`（加 get_allowed 测试）
- Test: `tests/modules/masterdata/test_routing_pages.py`（若存在，加 allowed 多选页面测试）

**Interfaces:**
- Consumes: `OperationWorkStationRepository.list_by_operation` (Task 1)
- Produces:
  - `MasterDataQueryService.get_allowed_work_stations(operation_id: int) -> list[WorkStation]`

- [ ] **Step 1: 加 get_allowed_work_stations 到 query_service**

`MasterDataQueryService.__init__` 加 `self._op_ws = OperationWorkStationRepository(db)`。在类末尾加：
```python
    def get_allowed_work_stations(self, operation_id: int) -> list[WorkStation]:
        rows = self._op_ws.list_by_operation(operation_id)
        ws_ids = [r.work_station_id for r in rows]
        if not ws_ids:
            return []
        return [self._work_stations.get(i) for i in ws_ids
                if self._work_stations.get(i) is not None]
```
顶部 import 加 `OperationWorkStationRepository`。

- [ ] **Step 2: 加查询测试**

`tests/modules/masterdata/test_operation_work_station.py` 加：
```python
from lightmes.modules.masterdata.query_service import MasterDataQueryService


def test_get_allowed_work_stations(db_session):
    md, p, wss = _setup(db_session, allowed_ids=[0, 1])
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序",
                        default_work_station_id=wss[0].id,
                        allowed_work_station_ids=[wss[0].id, wss[1].id])]))
    db_session.flush()
    op = md.routings.operations_of(routing.id)[0]
    allowed = MasterDataQueryService(db_session).get_allowed_work_stations(op.id)
    assert {w.id for w in allowed} == {wss[0].id, wss[1].id}
    assert all(w.code for w in allowed)  # 带 code/name
```

- [ ] **Step 3: 运行确认 PASS**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_operation_work_station.py -v
```
5 PASS（4 原有 + 1 新增）。

- [ ] **Step 4: 主数据 UI 工序表单加 allowed 多选**

读 `src/lightmes/modules/masterdata/router.py:335-369` 的 `routings_create_page`：它用**并行数组**接收工序字段（`op_seq`/`op_code`/`op_name`/`op_ws`/`op_skill`/`op_level` 都是 `list[str]` Form，`zip_longest` 对齐）。allowed 同样要走并行数组模式：加一个 `op_allowed: list[str] = Form(default=[])`，每个元素是逗号分隔的 ws_id 串（如 "1,2"），路由里按逗号 split 还原为 `list[int]`。

路由改动（替换 335-363 段）：
```python
@router.post("/masterdata/routings", response_class=HTMLResponse)
def routings_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    product_id: int = Form(...),
    op_seq: list[str] = Form(default=[]),
    op_code: list[str] = Form(default=[]),
    op_name: list[str] = Form(default=[]),
    op_ws: list[str] = Form(default=[]),
    op_allowed: list[str] = Form(default=[]),  # 新增：逗号分隔 ws_id 串
    op_skill: list[str] = Form(default=[]),
    op_level: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = MasterDataService(db)
    try:
        operations = []
        for seq, c, n, ws, allowed_str, sk_id, lvl in zip_longest(
            op_seq, op_code, op_name, op_ws, op_allowed, op_skill, op_level, fillvalue=""
        ):
            if not c.strip() or not ws.strip():
                continue
            allowed_ids = [int(x) for x in allowed_str.split(",") if x.strip().isdigit()] if allowed_str.strip() else [int(ws)]
            operations.append(OperationCreate(
                seq=int(seq), code=c.strip(), name=n.strip(),
                default_work_station_id=int(ws),
                allowed_work_station_ids=allowed_ids,
                required_skill_id=int(sk_id) if sk_id.strip() else None,
                required_level=int(lvl) if lvl.strip() else None))
        routing = svc.create_routing(RoutingCreate(
            code=code, name=name, product_id=product_id, operations=operations))
    except ValueError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "masterdata/partials/routing_error.html", {"error": str(e)})
    # ... 后续既有渲染（按 364+ 行原样保留）
```
（注意：op_allowed 若空则默认 `[int(ws)]` 即默认站，等价旧行为；这样既有不传 op_allowed 的调用也不破坏。）

模板改动 `src/lightmes/templates/masterdata/routings.html`（或对应的工序编辑表单 partial）：在每个工序行的"默认作业站"输入旁加一个 allowed 文本输入框（接收逗号分隔 ws_id；后续可改为多选 select，本期取文本框最小可用）。读现有模板找到工序行结构，加：
```html
<input name="op_allowed" placeholder="允许作业站ID，逗号分隔" value="{{ ws_id }}">
```
（与 op_ws 一致，每行一个 op_allowed 输入；不填则路由里默认 `[int(ws)]`。）

- [ ] **Step 6: 运行页面测试 + 回归 + Commit**

跑既有 routing/operation 页面测试，确认 allowed 多选不破坏现有。新增一条页面测试：提交 allowed 多选 → 关联表写入。
```bash
git add src/lightmes/modules/masterdata/query_service.py src/lightmes/modules/masterdata/router.py src/lightmes/templates/masterdata tests/
git commit -m "feat: get_allowed_work_stations + operation form allowed multi-select"
```

---

### Task 3: 过站判定改写（防跳站 + off-station）+ StationOpView.allowed_work_stations + next_op_can_continue_here

**Files:**
- Modify: `src/lightmes/modules/production/operation_pass_service.py`（防跳站第二层 + next_op_can_continue_here）
- Modify: `src/lightmes/modules/production/station_service.py`（off-station 判定 + StationOpView.allowed_work_stations 填充）
- Modify: `src/lightmes/modules/production/schemas.py`（OperationPassResult.next_op_can_continue_here + StationOpView.allowed_work_stations）
- Modify: `src/lightmes/templates/production/station_view.html`（路径全景每个节点显示 allowed 站名）
- Test: `tests/modules/production/test_operation_work_station_pass.py`

**Interfaces:**
- Consumes: `MasterDataQueryService.get_allowed_work_stations(op_id)` (Task 2)
- Produces:
  - `pass_operation` 防跳站第二层：`ws_id in allowed_ids` 才通过
  - `StationService.load` off-station：`ws_id not in allowed_ids` → BusinessRuleError
  - `StationOpView.allowed_work_stations: list[str]`（作业站名列表）
  - `OperationPassResult.next_op_can_continue_here: bool`

- [ ] **Step 1: schemas 加字段**

`src/lightmes/modules/production/schemas.py`：
- `StationOpView` 加 `allowed_work_stations: list[str] = []`
- `OperationPassResult` 加 `next_op_can_continue_here: bool = False`

- [ ] **Step 2: 改 pass_operation 防跳站第二层**

`src/lightmes/modules/production/operation_pass_service.py` 第 73-80 行替换为：
```python
        # 5. 三层防跳站：作业站须属工单产线；该工序的允许作业站须含当前作业站
        ws = self.query.get_work_station(data.work_station_id)
        if ws is None:
            raise NotFoundError(f"作业站不存在: {data.work_station_id}")
        if ws.line_id != wo.line_id:
            raise BusinessRuleError("当前作业站不属于本工单产线")
        allowed = self.query.get_allowed_work_stations(expected.id)
        allowed_ids = [w.id for w in allowed]
        if not allowed_ids:
            allowed_ids = [expected.default_work_station_id]  # 兜底（关联表空时退化为旧行为）
        if data.work_station_id not in allowed_ids:
            names = "、".join(w.name for w in allowed) or f"作业站 #{expected.default_work_station_id}"
            raise BusinessRuleError(
                f"该 SN 当前工序 {expected.seq} {expected.name} "
                f"应在【{names}】之一作业站做，当前作业站不符")
```
在 return OperationPassResult 前，加 next_op_can_continue_here 计算。读现有 `remaining = [o for o in operations if o.seq > expected.seq]` 段，改为：
```python
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
        return OperationPassResult(
            sn=su.sn,
            passed_op=OpInfo(seq=expected.seq, name=expected.name,
                             work_station_id=expected.default_work_station_id),
            next_op=next_info, is_finished=su.status == "finished",
            work_order_status=wo.status, bound_count=bound_count,
            param_count=param_count,
            next_op_can_continue_here=next_op_can_continue_here,
        )
```

- [ ] **Step 3: 改 StationService.load off-station 判定**

`src/lightmes/modules/production/station_service.py` 第 65-75 行（is_off_station 计算 + raise）替换为：
```python
        if expected is not None:
            allowed = self.query.get_allowed_work_stations(expected.id)
            allowed_ids = [w.id for w in allowed]
            if not allowed_ids:
                allowed_ids = [expected.default_work_station_id]
            if work_station_id not in allowed_ids:
                names = "、".join(w.name for w in allowed) or f"作业站 #{expected.default_work_station_id}"
                raise BusinessRuleError(
                    f"该 SN 当前工序 {expected.seq} {expected.name} "
                    f"应在【{names}】之一作业站做，当前作业站不符")
```
同时 `StationOpView` 组装时（`op_views.append(...)` 段）填入 allowed_work_stations：
```python
            op_allowed = self.query.get_allowed_work_stations(o.id)
            op_views.append(StationOpView(
                seq=o.seq, name=o.name, code=o.code,
                work_station_id=o.default_work_station_id, status=st,
                allowed_work_stations=[w.name for w in op_allowed]
                                       or [self.query.get_work_station(o.default_work_station_id).name
                                           if self.query.get_work_station(o.default_work_station_id) else f"#{o.default_work_station_id}"],
            ))
```

- [ ] **Step 4: 路径全景模板显示 allowed**

`src/lightmes/templates/production/station_view.html` 工艺路径全景的 `.station__step-name` 后加 allowed 显示：
```html
<div class="station__step-name">{{ o.name }}</div>
{% if o.allowed_work_stations %}
<div class="station__step-allowed">可在：{{ o.allowed_work_stations|join('、') }}</div>
{% endif %}
```
app.css 加 `.station__step-allowed { font-size: 10px; color: #6b8a76; margin-top: 2px; }`。

- [ ] **Step 5: 写测试**

`tests/modules/production/test_operation_work_station_pass.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.station_service import StationService
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, allowed_specs):
    """allowed_specs: [(ws_idx_in_line, is_default)] per op; 单产线 n_ops 个作业站"""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    n_ws = max(max(spec[0] for spec in allowed_specs),
               max(spec[1] for spec in allowed_specs)) + 1 if allowed_specs else 1
    wss = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ws)]
    return md, p, line, wss


def _route_and_wo(db_session, allowed_specs_per_op, n_ops):
    md, p, line, wss = _setup(db_session, [s for spec in allowed_specs_per_op for s in spec])
    operations = []
    for i, spec in enumerate(allowed_specs_per_op):
        # spec: list of (ws_idx, is_default) — 一道工序可多站
        allowed_ids = [wss[idx].id for idx, _ in spec]
        default_idx = next(idx for idx, is_def in spec if is_def)
        operations.append(OperationCreate(
            seq=(i+1)*10, code=f"OP{i+1}", name=f"工序{i+1}",
            default_work_station_id=wss[default_idx].id,
            allowed_work_station_ids=allowed_ids))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=operations))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id,
        line_id=line.id, qty=2, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    return md, p, line, wss, wo


def test_pass_allowed_station_passes(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [[(0, True), (1, False)]], 1)
    svc = OperationPassService(db_session)
    # 在 wss[1]（允许的第二站）首件过站 → 通过
    r = svc.pass_operation(OperationPassInput(
        work_station_id=wss[1].id, work_order_code="WO"))
    assert r.sn == "SN00001"


def test_pass_disallowed_station_rejected(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [[(0, True)]], 1)  # OP10 只允许 wss[0]
    svc = OperationPassService(db_session)
    with pytest.raises(BusinessRuleError, match="应在"):
        svc.pass_operation(OperationPassInput(
            work_station_id=wss[1].id, work_order_code="WO"))  # wss[1] 不在 allowed


def test_load_off_station_raises_with_allowed_names(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [[(0, True), (1, False)]], 1)
    svc = OperationPassService(db_session)
    # 先在 wss[0] 过 OP10
    r = svc.pass_operation(OperationPassInput(work_station_id=wss[0].id, work_order_code="WO"))
    db_session.flush()
    # 单工序路线，已完工 → SN00001 finished；改成 2 工序场景测 off-station
    # 这里改用 2 工序路线重测
    md2, p2, line2, wss2, wo2 = _route_and_wo(db_session, [
        [(0, True)],  # OP10 只允许 wss2[0]
        [(1, True)],  # OP20 只允许 wss2[1]
    ], 2)
    svc2 = OperationPassService(db_session)
    svc2.pass_operation(OperationPassInput(work_station_id=wss2[0].id, work_order_code="WO2"))
    # 单元现在在 OP20@wss2[1]，在 wss2[0] 扫 SN → off-station 抛错
    from lightmes.modules.production.repository import SerialUnitRepository
    su = SerialUnitRepository(db_session).list_by_work_order(wo2.id)[0]
    with pytest.raises(BusinessRuleError, match="应在"):
        StationService(db_session).load(su.sn, wss2[0].id, operator_id=None)


def test_next_op_can_continue_here(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [
        [(0, True)],            # OP10 只 wss[0]
        [(0, False), (1, True)]  # OP20 允许 wss[0] 和 wss[1]，默认 wss[1]
    ], 2)
    svc = OperationPassService(db_session)
    # 在 wss[0] 过 OP10 → next_op_can_continue_here=True（OP20 也允许 wss[0]）
    r = svc.pass_operation(OperationPassInput(work_station_id=wss[0].id, work_order_code="WO"))
    assert r.next_op_can_continue_here is True


def test_next_op_cannot_continue_here(db_session):
    md, p, line, wss, wo = _route_and_wo(db_session, [
        [(0, True)],  # OP10 wss[0]
        [(1, True)],  # OP20 只 wss[1]
    ], 2)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=wss[0].id, work_order_code="WO"))
    assert r.next_op_can_continue_here is False  # OP20 不允许 wss[0]
```

- [ ] **Step 6: 运行测试 + 回归 + Commit**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_operation_work_station_pass.py -v
```
预期 5 PASS。全量回归 → 全绿（既有单站工序测试，因 Task 1 已让 allowed=[default]，行为不变）。
```bash
git add src/lightmes/modules/production/operation_pass_service.py src/lightmes/modules/production/station_service.py src/lightmes/modules/production/schemas.py src/lightmes/templates/production/station_view.html src/lightmes/static/css/app.css tests/
git commit -m "feat: anti-skip by allowed set + off-station raises + next_op_can_continue_here + panorama allowed"
```

---

### Task 4: 连续过站 UX（station_pass 成功分流）

**Files:**
- Modify: `src/lightmes/modules/production/router.py`（station_pass 成功分流）
- Modify: `src/lightmes/templates/production/partials/station_pass_result.html`（加切站提示分支）
- Test: `tests/modules/production/test_station_main_flow.py`（加连续过站端到端测试）

**Interfaces:**
- Consumes: `OperationPassResult.next_op_can_continue_here` (Task 3)；`StationService.load` (既有)
- Produces: station_pass 成功分流三路（finished / continue-here / switch-station-prompt）

- [ ] **Step 1: 改 station_pass 成功分流**

`src/lightmes/modules/production/router.py` `station_pass` 成功渲染段（约 283-285 行）替换为：
```python
    su = SerialUnitRepository(db).get_by_sn(result.sn)
    wo_id = su.work_order_id if su is not None else None
    # 成功分流：finished → 完工片段；next_op 可在本站继续 → 刷富界面到下一工序；否则切站提示
    if result.is_finished:
        return templates.TemplateResponse(
            request, "production/partials/station_pass_result.html",
            {"result": result, "work_station_id": work_station_id, "work_order_id": wo_id},
        )
    if result.next_op_can_continue_here and su is not None:
        # 调 load 组装下一工序富界面（scan=SN，因 SN 一定能 get_by_sn 命中）
        try:
            view = StationService(db).load(su.sn, work_station_id, user.id)
        except DomainError as e:
            db.rollback()
            return templates.TemplateResponse(
                request, "production/partials/station_pass_result.html",
                {"error": e.detail, "work_station_id": work_station_id},
            )
        return templates.TemplateResponse(
            request, "production/station_view.html",
            {"view": view, "work_station_id": work_station_id,
             "just_passed": result.passed_op},
        )
    # 下一工序不在本站 → 切站提示
    return templates.TemplateResponse(
        request, "production/partials/station_pass_result.html",
        {"result": result, "work_station_id": work_station_id, "work_order_id": wo_id,
         "switch_station": True},
    )
```
（保留原 error 分支不变。）

- [ ] **Step 2: 加切站提示分支到 station_pass_result.html**

读现有 `src/lightmes/templates/production/partials/station_pass_result.html`，在完工/错误分支之外加切站分支：
```html
{% if error %}
<div class="alert alert--danger">✗ {{ error }}</div>
<div class="card"><div class="nav-card__desc">返回 <a href="/production/station?work_station_id={{ work_station_id }}">工位作业</a>。</div></div>
{% elif switch_station %}
<div class="alert alert--ok">
  ✓ <strong>{{ result.sn }}</strong> — 已过 工序{{ result.passed_op.seq }} {{ result.passed_op.name }}
  → 下一站：工序{{ result.next_op.seq }} {{ result.next_op.name }}（建议作业站：{{ result.next_op.work_station_id }}）
</div>
<div class="card">
  <div class="card__title">请切换作业站</div>
  <div class="nav-card__desc">该 SN 下一工序不在本作业站可做范围。请到对应作业站继续。
    返回 <a href="/production/station?work_station_id={{ work_station_id }}">工位作业</a>。</div>
</div>
{% elif result.is_finished %}
<div class="alert alert--ok">
  ✓ <strong>{{ result.sn }}</strong> — 已过 工序{{ result.passed_op.seq }} {{ result.passed_op.name }}
  <span class="badge">完工</span>
  {% if result.bound_count %}<span class="badge">绑定 {{ result.bound_count }} 组件</span>{% endif %}
  {% if result.param_count %}<span class="badge">录 {{ result.param_count }} 参数</span>{% endif %}
</div>
<div class="card"><div class="nav-card__desc">返回 <a href="/production/station?work_station_id={{ work_station_id }}">工位作业</a>。</div></div>
{% else %}
<!-- 既有"扫下一单元"分支（保留）：用于 finished 之外的非连续场景兜底 -->
<div class="alert alert--ok">
  ✓ <strong>{{ result.sn }}</strong> — 已过 工序{{ result.passed_op.seq }} {{ result.passed_op.name }}
  → 下一站：工序{{ result.next_op.seq }} {{ result.next_op.name }}
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
（顺序：error → switch_station → finished → 兜底扫下一单元。switch_station 必须在 is_finished 之前判断，因为 finished 时 next_op 是 None，但 switch_station 分支依赖 next_op——实际逻辑里 finished 不会进 switch_station 分支，因 station_pass 已分流。）

> 注意：station_pass 路由在 finished 分支也渲染本模板（不传 switch_station），所以模板分支顺序需保证 finished 优先于"兜底扫下一单元"。把 is_finished 分支放 switch_station 之后、else 之前即可。

- [ ] **Step 3: 写端到端测试**

`tests/modules/production/test_station_main_flow.py` 加：
```python
def test_e2e_continue_same_station_after_pass(client, db_session):
    """同站连续过站：OP10+OP20 都允许 ws[0] → 过 OP10 后富界面刷到 OP20"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PC", name="件", type="finished"))
    line = md.create_line(LineCreate(code="LC", name="线"))
    ws0 = md.create_work_station(WorkStationCreate(code="WC0", name="站0", line_id=line.id, seq=1))
    ws1 = md.create_work_station(WorkStationCreate(code="WC1", name="站1", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RTC", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws0.id, allowed_work_station_ids=[ws0.id]),
        OperationCreate(seq=20, code="OP20", name="工序20",
                        default_work_station_id=ws0.id, allowed_work_station_ids=[ws0.id, ws1.id]),
    ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SRC", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WOC", product_id=p.id, routing_id=routing.id,
        line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    _login(client, db_session)
    # 首件 enter：扫载体码绑 SN00001
    client.post("/production/station/enter",
                data={"work_station_id": str(ws0.id), "work_order_id": str(wo.id), "scan": "PAL-1"})
    # PASS OP10 → 富界面应刷新到 OP20（含"工序20"字样，且不再有"扫下一单元"）
    r = client.post("/production/station/pass",
                    data={"work_station_id": str(ws0.id), "scan": "PAL-1"})
    assert r.status_code == 200
    assert "工序20" in r.text and "当前" in r.text  # 富界面 OP20 当前
    assert "扫下一" not in r.text  # 没回扫码页


def test_e2e_switch_station_prompt_after_pass(client, db_session):
    """下一工序不在本站 → 切站提示"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PS", name="件", type="finished"))
    line = md.create_line(LineCreate(code="LS", name="线"))
    ws0 = md.create_work_station(WorkStationCreate(code="WS0", name="站0", line_id=line.id, seq=1))
    ws1 = md.create_work_station(WorkStationCreate(code="WS1", name="站1", line_id=line.id, seq=2))
    routing = md.create_routing(RoutingCreate(code="RTS", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序10",
                        default_work_station_id=ws0.id, allowed_work_station_ids=[ws0.id]),
        OperationCreate(seq=20, code="OP20", name="工序20",
                        default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
    ]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SRS", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WOS", product_id=p.id, routing_id=routing.id,
        line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    db_session.flush()
    _login(client, db_session)
    client.post("/production/station/enter",
                data={"work_station_id": str(ws0.id), "work_order_id": str(wo.id), "scan": "PAL-1"})
    r = client.post("/production/station/pass",
                    data={"work_station_id": str(ws0.id), "scan": "PAL-1"})
    assert r.status_code == 200
    assert "切换作业站" in r.text or "下一站" in r.text  # 切站提示
    assert "扫下一" not in r.text  # 不是连续扫码分支
```

- [ ] **Step 4: 运行测试 + 回归 + Commit**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/production/test_station_main_flow.py -v
```
全绿（含既有 + 2 新增）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/production/router.py src/lightmes/templates/production/partials/station_pass_result.html tests/
git commit -m "feat: station_pass success three-way split (finished / continue-here / switch-station)"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2g spec §3/§4/§5/§6/§7）：
- operation_work_stations 关联表 + UniqueConstraint + 数据迁移 → Task 1 ✅
- OperationCreate.allowed_work_station_ids + default ∈ allowed 校验 → Task 1 ✅
- create_routing 写关联表 → Task 1 ✅
- get_allowed_work_stations 查询 → Task 2 ✅
- 主数据维护 UI allowed 多选 → Task 2 ✅
- pass_operation 防跳站第二层 `ws_id in allowed` → Task 3 ✅
- StationService.load off-station 同步 → Task 3 ✅
- StationOpView.allowed_work_stations + 全景显示 → Task 3 ✅
- OperationPassResult.next_op_can_continue_here → Task 3 ✅
- station_pass 成功三路分流 → Task 4 ✅
- 切站提示模板分支 → Task 4 ✅
- 端到端连续过站 + 切站 → Task 4 ✅

**占位符扫描**：所有 code step 含完整代码。Task 2 Step 5 的工序路由接收 allowed 用了"以 grep 实际为准"的描述——这是因为 masterdata/router.py 的工序创建处理器签名需要 implementer 实地确认（约 350-365 行）。但给出了完整参数列表 + OperationCreate 组装规则，implementer 有足够信息。

**类型一致性**：`OperationWorkStation(operation_id, work_station_id)`、`OperationWorkStationRepository.add(op_id, ws_id)/list_by_operation(op_id)`、`get_allowed_work_stations(op_id) -> list[WorkStation]`、`OperationCreate.allowed_work_station_ids: list[int]`、`StationOpView.allowed_work_stations: list[str]`、`OperationPassResult.next_op_can_continue_here: bool` —— 定义处（Task 1/2/3）与引用处（Task 2/3/4）一致 ✅。

**关键回归风险**（已在 Task 1 Step 7 标注）：现有所有 `OperationCreate(...)` 调用方（测试 + UI 路由）未传 allowed_work_station_ids，Pydantic 会报错。Task 1 顺带把这些调用方都加 `allowed_work_station_ids=[default_work_station_id]` 单元素列表（等价旧行为）。Task 1 全量回归必须全绿才能进 Task 2。
