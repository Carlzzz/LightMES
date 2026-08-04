# P2a 层级模型重构 + 追溯体系重建 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 LightMES 从两层模型（routing→routing_step→station）推倒重建为物理/工艺分离的三层模型（产线 line→作业站 work_station；工艺路径 routing→工序 operation，工序分配到作业站），工单绑产线，并把追溯体系重落到工序记录（operation_record，追溯最小单位），复用 P1 经终审锤炼的全部逻辑。

**Architecture:** 推倒重建。drop 旧的 stations/routing_steps/station_passes/serial_units(旧结构)/genealogy_binds(旧 FK)，建新表。P1 的过站/绑料/返工/追溯逻辑思路全部保留，校验升级为三层（作业站属产线 + 工序默认作业站匹配），追溯挂 operation_record。沿用模块化单体全部约定（facade 跨模块读、领域异常、事件总线、乐观锁 guarded UPDATE、真实 DB 集成测试）。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, PostgreSQL+TimescaleDB, pytest, uv。

## Global Constraints

- Python 3.12；依赖用 `uv`（`uv run`）。测试/迁移命令用 `127.0.0.1`（非 localhost，避免 Windows IPv6 ~130s 卡顿）：
  `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run <cmd>`
- SQLAlchemy 2.0：`Mapped[]`/`mapped_column()`，继承 `lightmes.shared.base.Base`+`TimestampMixin`。
- 所有 schema 变更走 Alembic；模型已在 `src/lightmes/migrations/env.py` 注册（masterdata/production/trace 均已 import）。因推倒重建，迁移会 drop 旧表建新表；每次 autogenerate 后**打开迁移确认只动预期表**（沿用元数据对齐纪律，防部分索引漂移）。
- **推倒重建**：旧表可直接 drop（无生产数据）。旧模型类（Station/RoutingStep/StationPass）删除或替换，不保留兼容层（YAGNI）。
- 跨模块读 masterdata **只走 `MasterDataQueryService`**；production→trace 调 `GenealogyService`；trace→production 只读用其 repository（沿用 P1c）。
- 领域异常体系（`NotFoundError`/`ConflictError`/`ValidationError`/`BusinessRuleError`）+ 全局 handler 沿用。
- 事务边界在 `get_db`；repository 只 `flush()`；乐观锁 guarded UPDATE（`WHERE version=prev` + rowcount→ConflictError）沿用。
- 事件总线沿用；事件名 `StationPassed`→`OperationPassed`，其余 `SerialUnitFinished`/`GenealogyBound`/`GenealogyUnbound`/`SerialUnitReworkStarted` 保留。
- 集成测试连真实 PostgreSQL（`db_session` fixture）。TDD、频繁提交（前缀 `feat:`/`refactor:`/`chore:`/`test:`）。
- 预留字段（operation 的 required_skill_id/required_level/sop_id/panels）建表即可，P2a 不写读写逻辑。
- 旧扫码/追溯/返工**页面 + 其 router 端点**：P2a 会因模型改动失效。P2a 策略：**临时下线**——从各模块 router 移除 HTML 页面路由与依赖旧模型的 API，保留 service 层 + 保证 app 能启动、`/health` 与首页可访问、全量测试绿。正式富界面 P2d 重做。
- Shell 用 bash 语法。DB 需 running。

---

## File Structure

P2a 结束时的模块布局（新增/重构）：

```
src/lightmes/modules/masterdata/
├── models.py       # 重构：Product/Routing/Bom/BomItem 保留；删 Station/RoutingStep；加 Line/WorkStation/Operation
├── repository.py   # 重构：删 StationRepository/RoutingRepository.steps_of；加 Line/WorkStation/Operation repo；Routing.operations_of
├── query_service.py# 扩展：get_line/get_work_station/get_operations(按seq)；删 get_ordered_steps
├── schemas.py      # 重构：删 Station/RoutingStep schema；加 Line/WorkStation/Operation schema
├── service.py      # 重构：create_line/create_work_station；create_routing 用 operations
└── router.py       # 临时下线 HTML 页面（保留必要 API 或全下线）
src/lightmes/modules/production/
├── models.py       # 重构：WorkOrder 加 line_id；SerialUnit(current_operation_seq)；删 StationPass；加 OperationRecord/OperationParam
├── repository.py   # 重构：SerialUnit(list_in_process_by_work_station)；OperationRecord/OperationParam repo；删 StationPassRepository
├── schemas.py      # 重构：过站输入/结果换 work_station_id/operation；加 ParamInput
├── operation_pass_service.py  # 新建（替代 station_pass_service.py）：三层校验链
└── router.py       # 临时下线 HTML 页面
src/lightmes/modules/trace/
├── models.py       # 重构：genealogy_bind.station_pass_id → operation_record_id
├── repository.py   # 保留（查询方法不变）
├── genealogy_service.py  # 重落：bind 挂 operation_record_id
├── trace_service.py      # 重落：履历用 operation_record；加 params_of
├── rework_service.py     # 重落：current_operation_seq
└── router.py       # 临时下线 HTML 页面
src/lightmes/migrations/versions/  # 一系列 drop-old + create-new 迁移
scripts/seed_demo_line.py          # 重写：产线+作业站+工序示范线
tests/modules/...                  # 各任务测试重落新模型
```

> 说明：本计划保留 production 模块内"过站服务"文件名从 `station_pass_service.py` 换为 `operation_pass_service.py`，语义更准；旧文件删除。

---

### Task 1: 物理层 Line + WorkStation 模型 + 迁移 + repository + facade

新增物理层两张表（产线、作业站），不触碰旧表——先把物理骨架立起来。

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`（加 Line, WorkStation）
- Modify: `src/lightmes/modules/masterdata/repository.py`（加 LineRepository, WorkStationRepository）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（加 LineCreate/LineRead/WorkStationCreate/WorkStationRead）
- Modify: `src/lightmes/modules/masterdata/service.py`（加 create_line/create_work_station）
- Modify: `src/lightmes/modules/masterdata/query_service.py`（加 get_line/get_work_station）
- Create: `src/lightmes/migrations/versions/<auto>_create_line_work_station.py`
- Test: `tests/modules/masterdata/test_line_work_station.py`

**Interfaces:**
- Consumes: `Base`/`TimestampMixin`。
- Produces:
  - `models.Line`（表 `lines`）：`id:int PK`, `code:str unique index`, `name:str`, `description:str|None`, `is_active:bool default True`
  - `models.WorkStation`（表 `work_stations`）：`id:int PK`, `code:str unique index`, `name:str`, `line_id:int FK lines.id`, `seq:int`, `description:str|None`, `is_active:bool default True`；唯一约束 `(line_id, seq)` 名 `uq_work_station_line_seq`
  - `repository.LineRepository(db)`：`add/get/get_by_code/list_all`
  - `repository.WorkStationRepository(db)`：`add/get/get_by_code/list_by_line(line_id, 按 seq)`
  - `schemas.LineCreate`(code/name/description?)、`LineRead`(+id,is_active)、`WorkStationCreate`(code/name/line_id/seq/description?)、`WorkStationRead`(+id,is_active)
  - `service.MasterDataService.create_line(data)->Line`（code 重复→ValueError）、`create_work_station(data)->WorkStation`（code 重复→ValueError；line 不存在→ValueError；(line,seq) 冲突由 DB 唯一约束兜底）
  - `query_service.MasterDataQueryService.get_line(id)->Line|None`、`get_work_station(id)->WorkStation|None`

- [ ] **Step 1: 加模型**

在 `masterdata/models.py` 顶部 import 确认含 `ForeignKey, UniqueConstraint`（已有 Index/text 等），追加：
```python
class Line(Base, TimestampMixin):
    __tablename__ = "lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class WorkStation(Base, TimestampMixin):
    __tablename__ = "work_stations"
    __table_args__ = (
        UniqueConstraint("line_id", "seq", name="uq_work_station_line_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    seq: Mapped[int] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
```

- [ ] **Step 2: 生成并应用迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create line and work_station"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移仅创建 `lines` + `work_stations`（含 FK 与 `uq_work_station_line_seq`）。打开确认无 spurious 操作（不动其他表）。

- [ ] **Step 3: 加 schemas**

在 `masterdata/schemas.py` 追加：
```python
class LineCreate(BaseModel):
    code: str
    name: str
    description: str | None = None


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None
    is_active: bool


class WorkStationCreate(BaseModel):
    code: str
    name: str
    line_id: int
    seq: int
    description: str | None = None


class WorkStationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    line_id: int
    seq: int
    description: str | None
    is_active: bool
```

- [ ] **Step 4: 加 repository**

在 `masterdata/repository.py` 追加（顶部 import 加 `Line, WorkStation`；确认有 `from sqlalchemy import select`）：
```python
class LineRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, line: Line) -> Line:
        self.db.add(line)
        self.db.flush()
        return line

    def get(self, id: int) -> Line | None:
        return self.db.get(Line, id)

    def get_by_code(self, code: str) -> Line | None:
        return self.db.execute(
            select(Line).where(Line.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[Line]:
        return list(self.db.execute(select(Line)).scalars().all())


class WorkStationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, ws: WorkStation) -> WorkStation:
        self.db.add(ws)
        self.db.flush()
        return ws

    def get(self, id: int) -> WorkStation | None:
        return self.db.get(WorkStation, id)

    def get_by_code(self, code: str) -> WorkStation | None:
        return self.db.execute(
            select(WorkStation).where(WorkStation.code == code)
        ).scalar_one_or_none()

    def list_by_line(self, line_id: int) -> list[WorkStation]:
        return list(self.db.execute(
            select(WorkStation)
            .where(WorkStation.line_id == line_id)
            .order_by(WorkStation.seq)
        ).scalars().all())
```

- [ ] **Step 5: 加 service 方法**

在 `masterdata/service.py`（import 加 `Line, WorkStation, LineRepository, WorkStationRepository, LineCreate, WorkStationCreate`），`__init__` 加 `self.lines = LineRepository(db)`、`self.work_stations = WorkStationRepository(db)`，并加：
```python
    def create_line(self, data: LineCreate) -> Line:
        if self.lines.get_by_code(data.code) is not None:
            raise ValueError(f"产线编码已存在: {data.code}")
        line = Line(code=data.code, name=data.name, description=data.description)
        return self.lines.add(line)

    def create_work_station(self, data: WorkStationCreate) -> WorkStation:
        if self.work_stations.get_by_code(data.code) is not None:
            raise ValueError(f"作业站编码已存在: {data.code}")
        if self.lines.get(data.line_id) is None:
            raise ValueError(f"产线不存在: {data.line_id}")
        ws = WorkStation(
            code=data.code, name=data.name, line_id=data.line_id,
            seq=data.seq, description=data.description,
        )
        return self.work_stations.add(ws)
```

- [ ] **Step 6: 扩展 facade**

在 `masterdata/query_service.py`（import 加 `Line, WorkStation`），`__init__` 加 `self._lines = LineRepository(db)`、`self._work_stations = WorkStationRepository(db)`（import 加这两个 repo），并加：
```python
    def get_line(self, line_id: int) -> Line | None:
        return self._lines.get(line_id)

    def get_work_station(self, work_station_id: int) -> WorkStation | None:
        return self._work_stations.get(work_station_id)
```

- [ ] **Step 7: 写失败测试**

`tests/modules/masterdata/test_line_work_station.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import LineCreate, WorkStationCreate
from lightmes.modules.masterdata.query_service import MasterDataQueryService


def test_create_line_and_work_stations_ordered(db_session):
    svc = MasterDataService(db_session)
    line = svc.create_line(LineCreate(code="LINE-1", name="总装线1"))
    assert line.id is not None
    ws2 = svc.create_work_station(WorkStationCreate(
        code="WS-2", name="装配站", line_id=line.id, seq=2))
    ws1 = svc.create_work_station(WorkStationCreate(
        code="WS-1", name="上料站", line_id=line.id, seq=1))
    stations = svc.work_stations.list_by_line(line.id)
    assert [w.seq for w in stations] == [1, 2]
    assert stations[0].code == "WS-1"


def test_duplicate_line_code_rejected(db_session):
    svc = MasterDataService(db_session)
    svc.create_line(LineCreate(code="DUP-L", name="x"))
    with pytest.raises(ValueError):
        svc.create_line(LineCreate(code="DUP-L", name="y"))


def test_work_station_unknown_line_rejected(db_session):
    svc = MasterDataService(db_session)
    with pytest.raises(ValueError):
        svc.create_work_station(WorkStationCreate(
            code="WS-X", name="x", line_id=999999, seq=1))


def test_facade_get_line_and_station(db_session):
    svc = MasterDataService(db_session)
    line = svc.create_line(LineCreate(code="LINE-Q", name="查询线"))
    ws = svc.create_work_station(WorkStationCreate(
        code="WS-Q", name="站", line_id=line.id, seq=1))
    q = MasterDataQueryService(db_session)
    assert q.get_line(line.id).code == "LINE-Q"
    assert q.get_work_station(ws.id).code == "WS-Q"
    assert q.get_line(999999) is None
```

- [ ] **Step 8: 运行测试确认失败→通过 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/masterdata/test_line_work_station.py -v`
先失败（模型/方法未加），实现后通过（4 passed）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/masterdata src/lightmes/migrations tests/modules/masterdata/test_line_work_station.py
git commit -m "feat: add physical layer Line and WorkStation models"
```

---

### Task 2: 工艺层 RoutingStep→Operation 重构（drop 旧 stations/routing_steps）

把工序从 `routing_step`（指向 station）升级为 `operation`（分配到 work_station，加预留字段）。删除旧 `Station`/`RoutingStep` 模型与表。

> 依赖顺序说明：`RoutingStep`/`Station` 被 production 的 StationPass、trace 的 genealogy 通过 FK 间接引用。本任务只改 masterdata 层并 drop routing_steps + stations 表；production/trace 对旧表的引用在 Task 3-5 处理。为让本任务迁移能 drop stations，需先确保没有其它表 FK 指向 stations——实际 `serial_units.current_station_id` 和 `station_passes.station_id` 指向 stations。**因此本任务的迁移放到 Task 3/4 drop 掉 station_passes/旧 serial_units 之后**——即本任务先只改 masterdata 的模型代码与 operation 建表，`drop stations` 的迁移步骤合并到 Task 3（那时 serial_units/station_passes 已重建，不再引用 stations）。见下方 Step 说明。

**Files:**
- Modify: `src/lightmes/modules/masterdata/models.py`（删 Station, RoutingStep；加 Operation）
- Modify: `src/lightmes/modules/masterdata/repository.py`（删 StationRepository；RoutingRepository.steps_of→operations_of；加 OperationRepository）
- Modify: `src/lightmes/modules/masterdata/schemas.py`（删 Station*/RoutingStep*；加 Operation*；RoutingCreate 用 operations）
- Modify: `src/lightmes/modules/masterdata/service.py`（删 create_station；create_routing 用 operations + 校验 work_station）
- Modify: `src/lightmes/modules/masterdata/query_service.py`（删 get_ordered_steps；加 get_operations）
- Modify: `src/lightmes/modules/masterdata/router.py`（删依赖 Station/RoutingStep 的页面/API——临时下线）
- Create: 迁移（建 operations 表；drop routing_steps 表）
- Test: `tests/modules/masterdata/test_operation.py`（替代旧 test_routing 中 step 相关）

**Interfaces:**
- Consumes: `Line`/`WorkStation`（Task 1）；`Routing`（保留）。
- Produces:
  - `models.Operation`（表 `operations`）：`id:int PK`, `routing_id:int FK routings.id`, `seq:int`, `code:str`, `name:str`, `default_work_station_id:int FK work_stations.id`, `is_mandatory:bool default True`, `required_skill_id:int|None default None`, `required_level:int|None default None`, `sop_id:int|None default None`, `panels` (JSON, default None)；唯一约束 `(routing_id, seq)` 名 `uq_operation_routing_seq`
  - `repository.OperationRepository(db)`：`operations_of(routing_id, 按 seq)`（也可保留在 RoutingRepository，见 Step）
  - `repository.RoutingRepository.operations_of(routing_id)->list[Operation]`（按 seq）
  - `schemas.OperationCreate`(seq/code/name/default_work_station_id/is_mandatory default True)、`OperationRead`(+id, routing_id)、`RoutingCreate`(code/name/product_id/version default "1"/operations: list[OperationCreate])、`RoutingRead`(含 operations 按 seq)
  - `service.MasterDataService.create_routing(data: RoutingCreate)->Routing`：校验 code 唯一、product 存在、每个 operation 的 default_work_station 存在、seq 无重复；同产品单一 active 沿用。
  - `query_service.get_operations(routing_id)->list[Operation]`（按 seq；替代 get_ordered_steps）
  - **删除**：`models.Station`、`models.RoutingStep`、`StationRepository`、`get_ordered_steps`、`create_station`、Station/RoutingStep schema、依赖它们的 masterdata 页面路由。

- [ ] **Step 1: 加 Operation 模型，删 Station/RoutingStep**

在 `masterdata/models.py`：删除 `Station` 与 `RoutingStep` 两个类；加（import 确认含 `JSON`）：
```python
class Operation(Base, TimestampMixin):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("routing_id", "seq", name="uq_operation_routing_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    seq: Mapped[int] = mapped_column()
    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    default_work_station_id: Mapped[int] = mapped_column(
        ForeignKey("work_stations.id")
    )
    is_mandatory: Mapped[bool] = mapped_column(default=True)
    # 预留（P2c/P2d 填逻辑，本期仅建列）
    required_skill_id: Mapped[int | None] = mapped_column(default=None)
    required_level: Mapped[int | None] = mapped_column(default=None)
    sop_id: Mapped[int | None] = mapped_column(default=None)
    panels: Mapped[dict | None] = mapped_column(JSON, default=None)
```

- [ ] **Step 2: schemas 重构**

在 `masterdata/schemas.py`：删除 `StationCreate/StationRead/RoutingStepCreate/RoutingStepRead`；把 `RoutingCreate/RoutingRead` 改为用 operations，并加 Operation schema：
```python
class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    is_mandatory: bool = True


class OperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    routing_id: int
    seq: int
    code: str
    name: str
    default_work_station_id: int
    is_mandatory: bool


class RoutingCreate(BaseModel):
    code: str
    name: str
    product_id: int
    version: str = "1"
    operations: list[OperationCreate]


class RoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    product_id: int
    version: str
    status: str
    operations: list[OperationRead]
```

- [ ] **Step 3: repository 重构**

在 `masterdata/repository.py`：删除 `StationRepository`；把 `RoutingRepository.steps_of` 改名/改为 `operations_of`（返回 Operation 按 seq）；import 用 `Operation` 替换 `RoutingStep`、删 `Station`：
```python
    def operations_of(self, routing_id: int) -> list[Operation]:
        return list(
            self.db.execute(
                select(Operation)
                .where(Operation.routing_id == routing_id)
                .order_by(Operation.seq)
            ).scalars().all()
        )
```

- [ ] **Step 4: service 重构**

在 `masterdata/service.py`：删除 `create_station`（Station 没了）；`create_routing` 改用 operations 并校验 default_work_station 存在：
```python
    def create_routing(self, data: RoutingCreate) -> Routing:
        if self.routings.get_by_code(data.code) is not None:
            raise ValueError(f"路线编码已存在: {data.code}")
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        seqs = [o.seq for o in data.operations]
        if len(seqs) != len(set(seqs)):
            raise ValueError("工序 seq 不能重复")
        for op in data.operations:
            if self.work_stations.get(op.default_work_station_id) is None:
                raise ValueError(f"作业站不存在: {op.default_work_station_id}")
        has_active = self.routings.get_active_by_product(data.product_id) is not None
        routing = Routing(
            code=data.code, name=data.name, product_id=data.product_id,
            version=data.version, status="inactive" if has_active else "active",
        )
        self.routings.add(routing)
        for op in data.operations:
            self.db.add(Operation(
                routing_id=routing.id, seq=op.seq, code=op.code, name=op.name,
                default_work_station_id=op.default_work_station_id,
                is_mandatory=op.is_mandatory,
            ))
        self.db.flush()
        return routing
```
（import 用 `Operation` 替换 `RoutingStep`；`self.work_stations` 已在 Task 1 加。）

- [ ] **Step 5: facade + router**

`query_service.py`：删 `get_ordered_steps`，加 `get_operations`：
```python
    def get_operations(self, routing_id: int) -> list[Operation]:
        return self._routings.operations_of(routing_id)
```
（import 用 `Operation` 替换 `RoutingStep`。）
`masterdata/router.py`：移除任何依赖 Station/RoutingStep 的页面路由与 API（工位管理页、路线里 step 相关）。product 相关页面可保留（不依赖旧模型）。**临时下线**——本期不重做 masterdata UI（P2b）。若某端点仅因引用旧模型而报错，删除该端点。

- [ ] **Step 6: 迁移（建 operations；drop routing_steps）**

routing_steps 无其它表 FK 依赖，可本任务 drop。stations 仍被 serial_units/station_passes 引用，**留到 Task 3 drop**。
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "create operations drop routing_steps"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: 迁移创建 `operations`（FK 到 routings/work_stations + uq_operation_routing_seq），drop `routing_steps`。**打开迁移确认**：不应出现 drop `stations`（那留给 Task 3，此时仍被引用，autogenerate 若尝试 drop stations 会因 production 模型仍引用而不一致——手动从迁移移除任何 stations 相关 drop，只保留 operations 建表 + routing_steps drop）。

- [ ] **Step 7: 写测试**

`tests/modules/masterdata/test_operation.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.masterdata.query_service import MasterDataQueryService


def _line_with_stations(svc):
    line = svc.create_line(LineCreate(code="OPL", name="线"))
    w1 = svc.create_work_station(WorkStationCreate(code="OPW1", name="站1", line_id=line.id, seq=1))
    w2 = svc.create_work_station(WorkStationCreate(code="OPW2", name="站2", line_id=line.id, seq=2))
    return line, w1, w2


def test_create_routing_with_operations_ordered(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="OPP", name="壳", type="finished"))
    line, w1, w2 = _line_with_stations(svc)
    r = svc.create_routing(RoutingCreate(code="OPR", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=2, code="OP2", name="装配", default_work_station_id=w2.id),
        OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id),
    ]))
    ops = MasterDataQueryService(db_session).get_operations(r.id)
    assert [o.seq for o in ops] == [1, 2]
    assert ops[0].default_work_station_id == w1.id


def test_duplicate_seq_rejected(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="OPP2", name="壳", type="finished"))
    line, w1, w2 = _line_with_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="OPR2", name="x", product_id=p.id, operations=[
            OperationCreate(seq=1, code="A", name="a", default_work_station_id=w1.id),
            OperationCreate(seq=1, code="B", name="b", default_work_station_id=w2.id),
        ]))


def test_unknown_work_station_rejected(db_session):
    svc = MasterDataService(db_session)
    p = svc.create_product(ProductCreate(code="OPP3", name="壳", type="finished"))
    _line_with_stations(svc)
    with pytest.raises(ValueError):
        svc.create_routing(RoutingCreate(code="OPR3", name="x", product_id=p.id, operations=[
            OperationCreate(seq=1, code="A", name="a", default_work_station_id=999999)]))
```
删除旧 `tests/modules/masterdata/test_routing.py`、`test_station.py`、`test_query_service.py` 中依赖 RoutingStep/Station/get_ordered_steps 的用例（改为本文件覆盖；若整文件失效则删文件）。

- [ ] **Step 8: 运行测试 + 回归 + Commit**

Run 本文件测试 → PASS（3）。全量回归 → 全绿（删掉的旧测试不再计入）。
```bash
git add src/lightmes/modules/masterdata src/lightmes/migrations tests/modules/masterdata
git commit -m "refactor: replace RoutingStep/Station with Operation assigned to WorkStation"
```

---

### Task 3: WorkOrder 加 line_id + SerialUnit 重构（current_operation_seq；drop stations）

工单绑产线；serial_unit 的 `current_station_id`→跟随新模型，`current_step_seq`→`current_operation_seq`。此任务 drop 旧 `stations` 表（此时先 drop 依赖它的 station_passes——但 station_passes 在 Task 4 才 drop，故本任务先重建 serial_unit 去掉 current_station_id 的 FK，drop stations 放 Task 4）。

> 迁移依赖梳理（重要）：`stations` 被 `serial_units.current_station_id` 和 `station_passes.station_id` 引用。本任务把 serial_unit 改为不再引用 stations（去掉 current_station_id，改 current_operation_seq int），但 station_passes 仍在（Task 4 才删）。因此 **drop stations 合并到 Task 4**（station_passes 删除后）。本任务迁移只：给 work_orders 加 line_id 列 + 改 serial_units 结构。

**Files:**
- Modify: `src/lightmes/modules/production/models.py`（WorkOrder 加 line_id；SerialUnit current_step_seq→current_operation_seq，去 current_station_id）
- Modify: `src/lightmes/modules/production/repository.py`（SerialUnit 查询改 by work_station 概念——见下）
- Modify: `src/lightmes/modules/production/schemas.py`（WorkOrderCreate 加 line_id）
- Modify: `src/lightmes/modules/production/service.py`（create_work_order 校验 line）
- Create: 迁移
- Test: `tests/modules/production/test_work_order_line.py`

**Interfaces:**
- Consumes: `Line`（Task 1）。
- Produces:
  - `WorkOrder` 加 `line_id:int FK lines.id`
  - `SerialUnit`：`current_operation_seq:int default 0`（替代 current_step_seq）；**删** `current_station_id`；保留 sn/work_order_id/product_id/status/version/is_counted
  - `SerialUnitRepository`：`list_by_work_order` 保留；`list_in_process_by_station` **删除**（WIP 按工位查改到 Task 6 用 operation_record 实现，或本期先只留 list_by_work_order）
  - `schemas.WorkOrderCreate` 加 `line_id:int`；`WorkOrderRead` 加 `line_id`
  - `ProductionService.create_work_order` 校验 line 存在（用 `db.get(Line, ...)` 经 facade 或直接，沿用现有 db.get 风格；本期用 MasterDataQueryService.get_line 更规范）

- [ ] **Step 1: 改模型**

`production/models.py`：`WorkOrder` 加 `line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))`（放在 routing_id 附近）。`SerialUnit`：把 `current_step_seq` 改名 `current_operation_seq`，删除 `current_station_id` 那两行。

- [ ] **Step 2: 迁移**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "work_order line_id and serial_unit operation_seq"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected: work_orders 加 line_id 列（+FK）；serial_units 加 current_operation_seq、drop current_station_id 列（含其 FK）、drop current_step_seq。**打开确认**不含 drop stations（留 Task 4）。因现有演示数据里 work_orders 已有行，加 NOT NULL line_id 需要 server_default 或先允许空——**MVP 做法**：因推倒重建、演示数据将由 Task 8 seed 重灌，可先 `DATABASE_URL=... uv run python -c "from lightmes.database import engine; from sqlalchemy import text; c=engine.connect(); c.execute(text('DELETE FROM station_passes; DELETE FROM genealogy_binds; DELETE FROM serial_units; DELETE FROM work_orders;')); c.commit()"` 清空事务数据，再应用迁移（line_id NOT NULL 无历史行冲突）。

- [ ] **Step 3: schemas + service**

`schemas.py`：`WorkOrderCreate` 加 `line_id: int`；`WorkOrderRead` 加 `line_id: int`。
`service.py` `create_work_order`：加校验
```python
        if self.db.get(Line, data.line_id) is None:
            raise ValueError(f"产线不存在: {data.line_id}")
```
并把 `WorkOrder(...)` 构造加 `line_id=data.line_id`。（import `Line`。）

- [ ] **Step 4: repository 调整**

`production/repository.py`：`SerialUnitRepository` 删除 `list_in_process_by_station`（依赖已删的 current_station_id）；`list_by_work_order` 保留。

- [ ] **Step 5: 写测试**

`tests/modules/production/test_work_order_line.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import WorkOrderCreate


def _setup(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="WLP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="WLL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="WLW1", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="WLR", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id)]))
    return p, line, r


def test_create_work_order_binds_line(db_session):
    p, line, r = _setup(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="WL-WO1", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10))
    assert wo.line_id == line.id
    assert wo.status == "created"


def test_create_work_order_unknown_line_rejected(db_session):
    p, line, r = _setup(db_session)
    with pytest.raises(ValueError):
        ProductionService(db_session).create_work_order(WorkOrderCreate(
            code="WL-WO2", product_id=p.id, routing_id=r.id, line_id=999999, qty=1))
```

- [ ] **Step 6: 运行测试 + 回归 + Commit**

Run → PASS（2）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/production src/lightmes/migrations tests/modules/production/test_work_order_line.py
git commit -m "feat: work order binds line; serial unit tracks operation seq"
```

---

### Task 4: OperationRecord + OperationParam 模型 + repository（drop station_passes + stations）

追溯最小单位 `operation_record`（替代 station_pass）+ 参数快照 `operation_param`。此任务删除旧 `station_passes` 表和 `StationPass` 模型；station_passes 删除后 stations 不再被引用 → 同迁移 drop stations。

> 注意：`genealogy_binds.station_pass_id` FK 指向 station_passes。本任务 drop station_passes 前，须先在同迁移里改 genealogy_binds（Task 5 才重挂 operation_record）。为解依赖，本任务迁移顺序：建 operation_records + operation_params → 改 genealogy_binds.station_pass_id 列为 operation_record_id（此时先 drop 旧 FK 列、加新列，数据清空无碍）→ drop station_passes → drop stations。genealogy_bind 模型代码改动在 Task 5，但**列变更迁移在本任务**（避免 station_passes 删不掉）。或更简单：本任务连同 Task 5 的 genealogy_bind 模型改动一起做——见 Step 1 说明。

**Files:**
- Modify: `src/lightmes/modules/production/models.py`（删 StationPass；加 OperationRecord, OperationParam）
- Modify: `src/lightmes/modules/production/repository.py`（删 StationPassRepository；加 OperationRecordRepository, OperationParamRepository）
- Modify: `src/lightmes/modules/trace/models.py`（genealogy_bind.station_pass_id→operation_record_id，FK 改指向 operation_records）
- Create: 迁移（建 operation_records/operation_params；改 genealogy_binds 列；drop station_passes；drop stations）
- Test: `tests/modules/production/test_operation_record.py`

**Interfaces:**
- Produces:
  - `models.OperationRecord`（表 `operation_records`）：`id PK`, `serial_unit_id FK serial_units.id`, `work_order_id FK work_orders.id`, `operation_id FK operations.id`, `work_station_id FK work_stations.id`, `line_id FK lines.id`, `operator_id FK users.id nullable`, `start_time datetime|None`, `end_time datetime server_default now`, `result str default "pass"`, `remark str|None`
  - `models.OperationParam`（表 `operation_params`）：`id PK`, `operation_record_id FK operation_records.id`, `param_key str`, `param_value str`, `unit str|None`, `source str default "manual"`, `recorded_at datetime server_default now`
  - `repository.OperationRecordRepository(db)`：`add`, `list_by_serial_unit(sn_id, 按 end_time)`
  - `repository.OperationParamRepository(db)`：`add`, `list_by_record(record_id)`, `list_by_serial_unit(sn_id)`（join operation_records，供工艺参数追溯）
  - `trace.models.GenealogyBind`：`station_pass_id` → `operation_record_id:int|None FK operation_records.id`
  - **删**：`StationPass`、`StationPassRepository`

- [ ] **Step 1: 改模型（production + trace genealogy）**

`production/models.py`：删除 `StationPass` 类；加（import 确认含 `DateTime, ForeignKey, func`）：
```python
class OperationRecord(Base, TimestampMixin):
    __tablename__ = "operation_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    result: Mapped[str] = mapped_column(default="pass")
    remark: Mapped[str | None] = mapped_column(default=None)


class OperationParam(Base, TimestampMixin):
    __tablename__ = "operation_params"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_record_id: Mapped[int] = mapped_column(
        ForeignKey("operation_records.id")
    )
    param_key: Mapped[str] = mapped_column()
    param_value: Mapped[str] = mapped_column()
    unit: Mapped[str | None] = mapped_column(default=None)
    source: Mapped[str] = mapped_column(default="manual")  # manual/auto
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```
`trace/models.py`：把 `station_pass_id` 那行改为
```python
    operation_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("operation_records.id"), default=None
    )
```
（删掉旧 station_pass_id 行；其余 genealogy_bind 字段/索引不变。）

- [ ] **Step 2: 迁移（建新表 + 改 genealogy 列 + drop 旧表）**

先清空事务数据（若 Task 3 未清），再生成迁移：
```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic revision --autogenerate -m "operation_record param, migrate genealogy, drop station_pass station"
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run alembic upgrade head
```
Expected 迁移含：create operation_records、operation_params；genealogy_binds drop column station_pass_id + add column operation_record_id(+FK)；drop station_passes；drop stations。**打开迁移人工核对**顺序正确（先 drop station_passes 再 drop stations；genealogy 列变更不依赖 station_passes 存在）。如 autogenerate 顺序不对，手工调整 op 顺序。确认不误删其它表/索引。

- [ ] **Step 3: repository**

`production/repository.py`：删 `StationPassRepository`；import 用 `OperationRecord, OperationParam` 替换 `StationPass`；加：
```python
class OperationRecordRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, rec: OperationRecord) -> OperationRecord:
        self.db.add(rec)
        self.db.flush()
        return rec

    def list_by_serial_unit(self, serial_unit_id: int) -> list[OperationRecord]:
        return list(self.db.execute(
            select(OperationRecord)
            .where(OperationRecord.serial_unit_id == serial_unit_id)
            .order_by(OperationRecord.end_time)
        ).scalars().all())


class OperationParamRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, param: OperationParam) -> OperationParam:
        self.db.add(param)
        self.db.flush()
        return param

    def list_by_record(self, record_id: int) -> list[OperationParam]:
        return list(self.db.execute(
            select(OperationParam).where(
                OperationParam.operation_record_id == record_id)
        ).scalars().all())

    def list_by_serial_unit(self, serial_unit_id: int) -> list[OperationParam]:
        return list(self.db.execute(
            select(OperationParam)
            .join(OperationRecord,
                  OperationParam.operation_record_id == OperationRecord.id)
            .where(OperationRecord.serial_unit_id == serial_unit_id)
            .order_by(OperationParam.recorded_at)
        ).scalars().all())
```

- [ ] **Step 4: 写测试**

`tests/modules/production/test_operation_record.py`:
```python
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import WorkOrderCreate
from lightmes.modules.production.models import (
    SerialUnit, OperationRecord, OperationParam,
)
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository, OperationParamRepository,
)


def _fixture(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="ORP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="ORL", name="线"))
    w1 = md.create_work_station(WorkStationCreate(code="ORW1", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="ORR", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=1, code="OP1", name="上料", default_work_station_id=w1.id)]))
    op = md.routings.operations_of(r.id)[0]
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="OR-WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=5))
    su = SerialUnitRepository(db_session).add(
        SerialUnit(sn="ORSN1", work_order_id=wo.id, product_id=p.id))
    return line, w1, op, wo, su


def test_operation_record_and_params(db_session):
    line, w1, op, wo, su = _fixture(db_session)
    rec = OperationRecordRepository(db_session).add(OperationRecord(
        serial_unit_id=su.id, work_order_id=wo.id, operation_id=op.id,
        work_station_id=w1.id, line_id=line.id, result="pass"))
    assert rec.id is not None
    prepo = OperationParamRepository(db_session)
    prepo.add(OperationParam(operation_record_id=rec.id,
              param_key="扭矩", param_value="1.2", unit="N·m", source="manual"))
    recs = OperationRecordRepository(db_session).list_by_serial_unit(su.id)
    assert [r.id for r in recs] == [rec.id]
    params = prepo.list_by_serial_unit(su.id)  # 工艺参数追溯：跨记录汇集
    assert len(params) == 1 and params[0].param_key == "扭矩"
```

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run → PASS。全量回归 → 全绿（旧 station_pass 相关测试此时应已随 Task 3/本任务删除或改写；若有残留引用 StationPass 的测试，删除之）。
```bash
git add src/lightmes/modules/production src/lightmes/modules/trace/models.py src/lightmes/migrations tests/modules/production/test_operation_record.py
git commit -m "feat: add OperationRecord and OperationParam; drop StationPass and Station"
```

---

### Task 5: GenealogyService 重落 operation_record_id

绑料记录改挂 operation_record（工序记录）。GenealogyService 的 `bind_components` 参数 `station_pass_id`→`operation_record_id`，逻辑（BOM 校验/类型/唯一件占用反查/事件）不变。

**Files:**
- Modify: `src/lightmes/modules/trace/genealogy_service.py`（参数名 + 写入字段）
- Modify: `src/lightmes/modules/trace/repository.py`（若查询引用 station_pass_id 则更新；预计无需改，查询按 parent/component）
- Test: `tests/modules/trace/test_genealogy_service.py`（更新调用参数名）

**Interfaces:**
- Consumes: `GenealogyBind.operation_record_id`（Task 4）。
- Produces:
  - `GenealogyService.bind_components(parent_su, components, operator_id, operation_record_id) -> list[GenealogyBind]`（参数 `station_pass_id` 改名 `operation_record_id`；写 `GenealogyBind(operation_record_id=...)`）
  - `unbind` 不变。

- [ ] **Step 1: 改 genealogy_service**

在 `trace/genealogy_service.py`：`bind_components` 签名的 `station_pass_id` 改为 `operation_record_id`；构造 `GenealogyBind(...)` 里 `station_pass_id=station_pass_id` 改为 `operation_record_id=operation_record_id`。其余（get_active_bom_items 校验、serial/batch 分支、list_active_by_component_sn 占用反查、GenealogyBound 事件）**不动**。

- [ ] **Step 2: 更新现有测试的参数名**

`tests/modules/trace/test_genealogy_service.py`：把所有 `bind_components(..., station_pass_id=None)` 调用改为 `operation_record_id=None`。逻辑断言不变。

- [ ] **Step 3: 运行测试 + 回归 + Commit**

Run: `DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest tests/modules/trace/test_genealogy_service.py -v` → PASS（原 7 项等价）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/trace/genealogy_service.py tests/modules/trace/test_genealogy_service.py
git commit -m "refactor: genealogy binds hang on operation_record instead of station_pass"
```

---

### Task 6: OperationPassService 三层过站校验链（替代 StationPassService）

新建 `operation_pass_service.py`，实现三层校验链（作业站属产线 + 工序默认作业站匹配），复用 P1 全部正确逻辑；删除旧 `station_pass_service.py`。含手动参数录入、SN 并发测试。

**Files:**
- Create: `src/lightmes/modules/production/operation_pass_service.py`
- Delete: `src/lightmes/modules/production/station_pass_service.py`
- Modify: `src/lightmes/modules/production/schemas.py`（过站输入/结果换新字段 + ParamInput）
- Modify: `src/lightmes/modules/production/events.py`（`StationPassed`→`OperationPassed`）
- Modify: `src/lightmes/modules/trace/__init__.py`（订阅 `OperationPassed` 替代 StationPassed）
- Test: `tests/modules/production/test_operation_pass.py`, `tests/modules/production/test_sn_concurrency.py`(更新 import)

**Interfaces:**
- Consumes: `MasterDataQueryService.get_operations/get_work_station/get_line`, `SnGenerator`, `SerialUnitRepository`, `OperationRecordRepository`, `OperationParamRepository`, `WorkOrderRepository`, `SnRuleRepository`, `GenealogyService`。
- Produces:
  - `schemas.ComponentInput`（保留：component_product_id/component_sn?/component_batch_no?/qty）
  - `schemas.ParamInput`（`param_key:str`, `param_value:str`, `unit:str|None=None`）
  - `schemas.OperationPassInput`（`work_station_id:int`, `work_order_code:str|None=None`, `sn:str|None=None`, `operator_id:int|None=None`, `components:list[ComponentInput]=[]`, `params:list[ParamInput]=[]`）
  - `schemas.OpInfo`（`seq:int`, `name:str`, `work_station_id:int`）
  - `schemas.OperationPassResult`（`sn`, `passed_op:OpInfo`, `next_op:OpInfo|None`, `is_finished:bool`, `work_order_status:str`, `bound_count:int=0`, `param_count:int=0`）
  - `events.OperationPassed`（`serial_unit_id, sn, work_order_id, operation_id, work_station_id, line_id`）；`SerialUnitFinished` 不变
  - `operation_pass_service.OperationPassService(db).pass_operation(data: OperationPassInput) -> OperationPassResult`

- [ ] **Step 1: schemas + events**

`production/schemas.py`：删除旧 `StationPassInput/StationPassResult/StepInfo`；加 `ParamInput`、`OperationPassInput`、`OpInfo`、`OperationPassResult`（ComponentInput 保留）。
`production/events.py`：`StationPassed` 改名 `OperationPassed`，字段改为 `serial_unit_id, sn, work_order_id, operation_id, work_station_id, line_id`；`SerialUnitFinished` 不变。
`trace/__init__.py`：订阅从 `StationPassed` 改 `OperationPassed`（handler 仍 no-op/日志）。

- [ ] **Step 2: 写失败测试（三层校验链）**

`tests/modules/production/test_operation_pass.py`:
```python
import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, ParamInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationParamRepository,
)
from lightmes.shared.errors import NotFoundError, BusinessRuleError


def _line(db_session, n_ops=2):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PPX", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PLX", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"PWX{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="PRX", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRLX", name="r", pattern="X{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PXWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return p, line, ws, wo


def test_first_pass_generates_sn_and_binds_and_params(db_session):
    p, line, ws, wo = _line(db_session, n_ops=2)
    svc = OperationPassService(db_session)
    res = svc.pass_operation(OperationPassInput(
        work_station_id=ws[0].id, work_order_code="PXWO",
        params=[ParamInput(param_key="温度", param_value="60", unit="℃")]))
    assert res.sn == "X0001"
    assert res.passed_op.seq == 1
    assert res.next_op.seq == 2
    assert res.param_count == 1
    params = OperationParamRepository(db_session).list_by_serial_unit(
        SerialUnitRepository(db_session).get_by_sn(res.sn).id)
    assert params[0].param_key == "温度"


def test_wrong_work_station_rejected(db_session):
    p, line, ws, wo = _line(db_session, n_ops=2)
    svc = OperationPassService(db_session)
    # 首件却扫到第二作业站 → 防跳站
    with pytest.raises(BusinessRuleError):
        svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, work_order_code="PXWO"))


def test_work_station_of_other_line_rejected(db_session):
    p, line, ws, wo = _line(db_session, n_ops=1)
    md = MasterDataService(db_session)
    other_line = md.create_line(LineCreate(code="OTHERL", name="别的线"))
    other_ws = md.create_work_station(WorkStationCreate(
        code="OTHERW", name="站", line_id=other_line.id, seq=1))
    svc = OperationPassService(db_session)
    # 用不属于工单产线的作业站过站 → 拒绝
    with pytest.raises(BusinessRuleError):
        svc.pass_operation(OperationPassInput(
            work_station_id=other_ws.id, work_order_code="PXWO"))


def test_full_route_finishes(db_session):
    p, line, ws, wo = _line(db_session, n_ops=2)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="PXWO"))
    r2 = svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, sn=r1.sn))
    assert r2.is_finished is True
    assert r2.next_op is None


def test_unknown_work_order_rejected(db_session):
    p, line, ws, wo = _line(db_session, n_ops=1)
    svc = OperationPassService(db_session)
    with pytest.raises(NotFoundError):
        svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="NOPE"))
```

- [ ] **Step 3: 运行确认失败，写 OperationPassService**

`src/lightmes/modules/production/operation_pass_service.py`:
```python
from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import (
    SerialUnit, OperationRecord, OperationParam, WorkOrder,
)
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository, OperationParamRepository,
    SnRuleRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    OperationPassInput, OperationPassResult, OpInfo,
)
from lightmes.modules.production.sn_generator import SnGenerator
from lightmes.modules.production.events import OperationPassed, SerialUnitFinished
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import NotFoundError, BusinessRuleError, ConflictError
from lightmes.shared.events import event_bus


class OperationPassService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.serial_units = SerialUnitRepository(db)
        self.records = OperationRecordRepository(db)
        self.params = OperationParamRepository(db)
        self.work_orders = WorkOrderRepository(db)
        self.sn_rules = SnRuleRepository(db)
        self.sn_gen = SnGenerator(db)

    def pass_operation(self, data: OperationPassInput) -> OperationPassResult:
        # 1+3. 定位工单与 SN
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                raise NotFoundError(f"SN 不存在: {data.sn}")
            if su.status in ("finished", "scrapped"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {data.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首件过站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = None

        # 2. 工单状态
        if wo is None:
            raise NotFoundError("工单不存在")
        if wo.status not in ("released", "in_process"):
            raise BusinessRuleError(f"工单状态不允许过站: {wo.status}")

        operations = self.query.get_operations(wo.routing_id)
        if not operations:
            raise BusinessRuleError("工艺路径无工序")

        # 3(续). 首件生成 SN
        if su is None:
            if wo.sn_rule_id is None:
                raise BusinessRuleError("工单未配置 SN 规则")
            rule = self.sn_rules.get(wo.sn_rule_id)
            if rule is None:
                raise BusinessRuleError("SN 规则不存在")
            new_sn = self.sn_gen.next_sn(rule)
            su = self.serial_units.add(SerialUnit(
                sn=new_sn, work_order_id=wo.id, product_id=wo.product_id,
                status="in_process", current_operation_seq=0,
            ))

        # 4. 期望下一工序（前向唯一→天然防重复）
        next_ops = [o for o in operations if o.seq > su.current_operation_seq]
        if not next_ops:
            raise BusinessRuleError("已完工，无后续工序")
        expected = next_ops[0]

        # 5. 三层防跳站：作业站须 = 期望工序默认作业站，且该作业站属工单产线
        ws = self.query.get_work_station(data.work_station_id)
        if ws is None:
            raise NotFoundError(f"作业站不存在: {data.work_station_id}")
        if ws.line_id != wo.line_id:
            raise BusinessRuleError("当前作业站不属于本工单产线")
        if data.work_station_id != expected.default_work_station_id:
            raise BusinessRuleError(
                f"应到工序 {expected.seq} {expected.name} 对应作业站，当前作业站不符")

        # 5b. 技能校验钩子（P2c 填；本期默认放行）
        # if expected.required_skill_id: ...

        # 6. 写工序记录 + 乐观锁更新 serial_unit
        record = self.records.add(OperationRecord(
            serial_unit_id=su.id, work_order_id=wo.id, operation_id=expected.id,
            work_station_id=data.work_station_id, line_id=wo.line_id,
            operator_id=data.operator_id, result="pass",
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

        # 7. 绑料（同事务，失败整单回滚）
        bound_count = 0
        if data.components:
            try:
                binds = GenealogyService(self.db).bind_components(
                    su,
                    [ComponentBind(
                        component_product_id=c.component_product_id,
                        component_sn=c.component_sn,
                        component_batch_no=c.component_batch_no,
                        qty=c.qty,
                    ) for c in data.components],
                    operator_id=data.operator_id,
                    operation_record_id=record.id,
                )
            except Exception:
                self.db.rollback()
                raise
            bound_count = len(binds)

        # 8. 参数录入
        param_count = 0
        for pm in data.params:
            self.params.add(OperationParam(
                operation_record_id=record.id, param_key=pm.param_key,
                param_value=pm.param_value, unit=pm.unit, source="manual",
            ))
            param_count += 1

        # 9. 末工序完工（防重复计数沿用 is_counted）
        is_last = expected.seq == operations[-1].seq
        if is_last:
            su.status = "finished"
            if not su.is_counted:
                su.is_counted = True
                event_bus.publish(SerialUnitFinished(
                    serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id))
                new_qty = self.db.execute(
                    update(WorkOrder).where(WorkOrder.id == wo.id)
                    .values(produced_qty=WorkOrder.produced_qty + 1)
                    .returning(WorkOrder.produced_qty)
                ).scalar_one()
                if new_qty >= wo.qty:
                    self.db.execute(update(WorkOrder).where(WorkOrder.id == wo.id)
                                    .values(status="completed"))
                self.db.refresh(wo)

        # 10. 工单/返工件状态复位
        if wo.status == "released":
            wo.status = "in_process"
        if su.status == "reworking":
            su.status = "in_process"
        self.db.flush()

        # 11. 事件
        event_bus.publish(OperationPassed(
            serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            operation_id=expected.id, work_station_id=data.work_station_id,
            line_id=wo.line_id))

        remaining = [o for o in operations if o.seq > expected.seq]
        next_info = (OpInfo(seq=remaining[0].seq, name=remaining[0].name,
                            work_station_id=remaining[0].default_work_station_id)
                     if remaining else None)
        return OperationPassResult(
            sn=su.sn,
            passed_op=OpInfo(seq=expected.seq, name=expected.name,
                             work_station_id=expected.default_work_station_id),
            next_op=next_info, is_finished=su.status == "finished",
            work_order_status=wo.status, bound_count=bound_count,
            param_count=param_count,
        )
```
删除 `src/lightmes/modules/production/station_pass_service.py`。更新 `tests/modules/production/test_sn_concurrency.py` 若 import 了旧服务则改；其纯测 SnGenerator，通常无需改。删除旧 `test_station_pass.py`/`test_station_pass_binding.py`/`test_station_pass_concurrency.py`（被本任务测试替代）——或改写为新服务；本计划选择删除旧文件、以 test_operation_pass.py 覆盖，多步返工并发在 Task 7 覆盖。

- [ ] **Step 4: 运行测试 + 回归 + Commit**

Run test_operation_pass.py → PASS（5）。全量回归 → 全绿。
```bash
git add src/lightmes/modules/production tests/modules/production
git rm src/lightmes/modules/production/station_pass_service.py
git commit -m "feat: OperationPassService with 3-layer station check, params, replaces StationPassService"
```

---

### Task 7: TraceService（履历/正反查/参数追溯）+ ReworkService 重落

追溯与返工重落新模型：履历用 operation_record + 参数快照；返工用 current_operation_seq。

**Files:**
- Modify: `src/lightmes/modules/trace/trace_service.py`（履历用 OperationRecord；加 params_of）
- Modify: `src/lightmes/modules/trace/schemas.py`（PassView→OpRecordView 含 operation/work_station；加 ParamView；HistoryView 含 params）
- Modify: `src/lightmes/modules/trace/rework_service.py`（current_step_seq→current_operation_seq）
- Test: `tests/modules/trace/test_trace_service.py`, `tests/modules/trace/test_rework_service.py`（重落新模型）

**Interfaces:**
- Consumes: `OperationRecordRepository`, `OperationParamRepository`, `GenealogyBindRepository`。
- Produces:
  - `schemas.OpRecordView`（`operation_id, work_station_id, line_id, result, end_time`）替代 PassView
  - `schemas.ParamView`（`param_key, param_value, unit, source, recorded_at`）
  - `schemas.HistoryView`（`sn`, `records: list[OpRecordView]`, `components: list[BindView]`, `params: list[ParamView]`）
  - `TraceService.genealogy_of`/`where_used` 不变（谱系查询按 parent/component，不依赖 record 类型）
  - `TraceService.history_of(sn)`：records 用 OperationRecordRepository.list_by_serial_unit；params 用 OperationParamRepository.list_by_serial_unit；components 用 list_by_parent
  - `TraceService.params_of(sn) -> list[ParamView]`（工艺参数追溯，可单列）
  - `ReworkService.rework`：`current_step_seq`→`current_operation_seq`（校验与乐观锁不变）

- [ ] **Step 1: schemas 重落**

`trace/schemas.py`：把 `PassView` 换为 `OpRecordView`（字段 operation_id/work_station_id/line_id/result/end_time），加 `ParamView`；`HistoryView` 字段改为 `records`/`components`/`params`。`BindView`/`GenealogyView`/`ParentRef` 不变。

- [ ] **Step 2: trace_service 重落**

`trace/trace_service.py`：`__init__` 用 `OperationRecordRepository`/`OperationParamRepository` 替换 `StationPassRepository`。`history_of` 改为：
```python
    def history_of(self, sn: str) -> HistoryView:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        records = self.records.list_by_serial_unit(su.id)
        binds = self.binds.list_by_parent(su.id)
        params = self.params.list_by_serial_unit(su.id)
        return HistoryView(
            sn=sn,
            records=[OpRecordView(
                operation_id=r.operation_id, work_station_id=r.work_station_id,
                line_id=r.line_id, result=r.result, end_time=r.end_time)
                for r in records],
            components=[_bind_view(b) for b in binds],
            params=[ParamView(
                param_key=p.param_key, param_value=p.param_value, unit=p.unit,
                source=p.source, recorded_at=p.recorded_at) for p in params],
        )

    def params_of(self, sn: str) -> list[ParamView]:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        return [ParamView(
            param_key=p.param_key, param_value=p.param_value, unit=p.unit,
            source=p.source, recorded_at=p.recorded_at)
            for p in self.params.list_by_serial_unit(su.id)]
```
`genealogy_of`/`where_used` 不变。

- [ ] **Step 3: rework_service 重落**

`trace/rework_service.py`：把 `su.current_step_seq` 全部改为 `su.current_operation_seq`（校验条件、UPDATE 的 values）。逻辑（乐观锁、解绑、事件、scrap）不变。

- [ ] **Step 4: 重落测试**

改写 `tests/modules/trace/test_trace_service.py`、`test_rework_service.py`，用新模型 fixture（line/work_station/operation + OperationPassService.pass_operation 驱动真实数据）覆盖：正向、反向召回（含 unbound）、履历（工序级 records + params）、工艺参数追溯（params_of）、多步返工重过、判废。保留 P1c 已验证的场景等价重跑。

示例（履历+参数+召回关键用例）：
```python
def test_history_and_params_and_recall(db_session):
    # 建线/站/工序/BOM/工单，pass_operation 带组件+参数过站
    # 断言 history_of.records 工序级、params 含录入参数、where_used 反查到成品
    ...
```
（完整用例参考 P1c 的 test_trace_service/test_rework_service 结构，替换为 line/work_station/operation + pass_operation。实现时逐一重落，确保断言等价。）

- [ ] **Step 5: 运行测试 + 回归 + Commit**

Run trace 测试 → PASS。全量回归 → 全绿。
```bash
git add src/lightmes/modules/trace tests/modules/trace
git commit -m "refactor: trace history and rework rebuilt on operation records and params"
```

---

### Task 8: seed 脚本重写 + 全量回归

重写示范线（产线+作业站+工序），跑通端到端，全量测试绿。

**Files:**
- Modify: `scripts/seed_demo_line.py`（用 line/work_station/operation）
- Test: 全量回归

- [ ] **Step 1: 重写 seed 脚本**

`scripts/seed_demo_line.py` 改为：建产线 `LINE-A`、作业站（上料/装配/检测，seq 1/2/3，均属 LINE-A）、成品 + 组件、BOM、工艺路径（3 工序分别分配到 3 作业站）、SN 规则、工单（绑 LINE-A + qty=10）并 release。打印 line_id/各 work_station_id/工单号，及"怎么玩"提示（用 OperationPassService 的 API/字段：work_station_id + work_order_code）。

- [ ] **Step 2: 跑 seed + 端到端冒烟**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run python scripts/seed_demo_line.py
```
Expected: 打印示范线信息，无异常。

- [ ] **Step 3: 全量回归**

```bash
DATABASE_URL="postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes" uv run pytest -v
```
Expected: 全绿（所有旧 station_pass/routing_step 相关测试已删或重落；新三层模型测试通过）。确认 app 能启动、`/health` 与首页可访问（旧业务页面已临时下线，不报错）。

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_demo_line.py
git commit -m "chore: rewrite seed for line/work_station/operation demo line"
```

---

## Self-Review 结果

**Spec 覆盖**（对照 P2a spec §2/§3/§4/§6）：
- line/work_station 模型 → Task 1 ✅
- routing_step→operation（default_work_station + 预留字段）→ Task 2 ✅
- work_order 绑 line、serial_unit current_operation_seq → Task 3 ✅
- operation_record（追溯最小单位）+ operation_param → Task 4 ✅
- genealogy 挂 operation_record → Task 4(模型)/Task 5(服务) ✅
- OperationPassService 三层校验链 + 手动参数 → Task 6 ✅
- TraceService 履历/正反查/参数追溯 + ReworkService → Task 7 ✅
- seed 重写 + 推倒重建迁移 → Task 8 + 各任务迁移 ✅
- 旧页面临时下线 → Task 2/6 router 处理 ✅
- 预留字段不实现逻辑 → Task 2（operation 建列不用）✅

**占位符扫描**：Task 7 Step 4 的测试用例给了结构方向而非逐行完整代码（因需重落 P1c 多个用例，属"参照既有结构重落"）——这是唯一非完全逐行处，已明确指出参照 P1c test 文件结构 + 替换模型。其余步骤均含完整代码。实现时按 P1c 对应测试等价重落。

**类型一致性**：`Line`/`WorkStation`/`Operation`、`OperationRecord`/`OperationParam`、`current_operation_seq`、`OperationPassInput/OperationPassResult/OpInfo/ParamInput/ComponentInput`、`OperationPassed`、`get_operations`/`get_line`/`get_work_station`、`bind_components(operation_record_id=)`、`OpRecordView/ParamView/HistoryView(records/params)`、`rework(current_operation_seq)` —— 定义处与引用处一致 ✅。

**迁移依赖链**（推倒重建的关键风险，已在任务内显式处理）：Task2 drop routing_steps；Task3 改 serial_units（去 current_station_id）+ work_orders 加 line_id + 清空事务数据；Task4 建 operation_records/params + 改 genealogy 列 + drop station_passes + drop stations（顺序：先 station_passes 后 stations）。每步 autogenerate 后人工核对迁移只动预期表。
