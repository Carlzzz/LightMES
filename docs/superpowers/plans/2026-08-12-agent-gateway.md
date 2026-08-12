# Agent Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 LightMES 上叠加 D 层 Agent Gateway：基于 `fastmcp` 在 `/mcp` 端点暴露 17 个 MCP 工具（13 thin wrappers + 4 compose），复用现有 C 层 Bearer token 认证 + scope/role 双重 gate。

**Architecture:** `fastmcp` 包提供 `FastMCP` 类，其 `http_app()` 方法返回 ASGI 应用，通过 `app.mount("/mcp", mcp_app, dependencies=[Depends(verify_bearer)])` 挂载到 FastAPI。verify_bearer 依赖验证 API Key + 注入 user/api_key/db_session 到 `request.state`，供 tool 函数通过 `ctx.get_request()` 访问。Tools 直接 import service 层（ProductionService / PlannerService / DefectService / ApiKeyService）。

**Tech Stack:** Python 3.12, FastAPI, fastmcp 2.x（MCP HTTP transport），SQLAlchemy 2.0, Pydantic v2, pytest, uv

## Global Constraints

- DATABASE_URL: `postgresql+psycopg://mes:mes@127.0.0.1:5432/lightmes`（127.0.0.1 not localhost — Windows IPv6 ~130s stall）
- 测试用 `db_session` fixture（SAVEPOINT isolation），不直接 commit；service 层可 commit
- 文案 Chinese for tool descriptions / error messages
- 复用 C 层 auth infrastructure：`ApiKeyService.validate()` from `lightmes.modules.api_v1.api_key_service`
- 复用 C 层 role gating helper：write scope 需 admin/supervisor 角色（同 `lightmes/modules/api_v1/dependencies.py` 中 `_has_write_role`）
- Tool 命名：snake_case（符合 MCP 命名约定）
- Tool 函数签名：必含 `ctx: Context = None` 参数（FastMCP 注入）
- 写工具必须 double-gate：scope (write) AND role (admin/supervisor)
- DomainError（ValidationError/NotFoundError/ConflictError/BusinessRuleError）→ 直接 raise，FastMCP 默认转为 MCP error response
- 写工具调用必须可审计（由 ApiCallLog middleware 在 `/mcp/*` 路径记录写 + 错误）
- `fastmcp` 包版本锁定 `>=2.0,<3.0`（2.0 stable 已发布）
- 所有新增工具通过 `tools/list` 自动可发现
- 模块注册通过 `lightmes/modules/agent_gateway/__init__.py` 的 `register(app)` 函数（新模块）
- `app.mount("/mcp", mcp_app, dependencies=[Depends(verify_bearer)])` 是关键集成点（FastMCP + FastAPI 的标准 mount 模式）
- `mcp_app.lifespan` 必须传给 FastAPI（否则 session manager 不初始化）

---

### Task 1: 依赖 + 模块结构 + register stub

**Files:**
- Modify: `pyproject.toml`（加 `fastmcp>=2.0,<3.0` 依赖）
- Create: `src/lightmes/modules/agent_gateway/__init__.py`（register 函数 stub）
- Create: `src/lightmes/modules/agent_gateway/server.py`（FastMCP 实例 + tools/__init__.py 空文件）
- Modify: `src/lightmes/main.py`（注册新模块 + lifespan 合并）
- Create: `tests/modules/agent_gateway/__init__.py`（空文件）

**Interfaces:**
- Consumes: 无
- Produces:
  - `fastmcp` 包依赖（`pip install fastmcp`）
  - `lightmes.modules.agent_gateway.register(app)` 函数（后续 task 填充）
  - `lightmes.modules.agent_gateway.server.mcp` FastMCP 实例
  - `/mcp` endpoint 挂载到 FastAPI（占位，没有工具）

- [ ] **Step 1: 加 fastmcp 依赖**

修改 `pyproject.toml`，在 `dependencies` 数组中添加：

```toml
dependencies = [
    "alembic>=1.18.5",
    "fastapi>=0.141.1",
    "fastmcp>=2.0,<3.0",
    # ... existing ...
]
```

执行 `uv sync` 安装。

验证：

```bash
uv run python -c "from fastmcp import FastMCP; print(FastMCP('test'))"
```

应输出 `FastMCP(name='test', version=None)` 之类。

- [ ] **Step 2: 创建 agent_gateway 模块结构**

创建 `src/lightmes/modules/agent_gateway/__init__.py`：

```python
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Mount MCP server at /mcp. Filled in by later tasks."""
    from lightmes.modules.agent_gateway.server import mount_mcp
    mount_mcp(app)
```

创建 `src/lightmes/modules/agent_gateway/server.py`：

```python
from fastmcp import FastMCP

mcp = FastMCP(
    name="LightMES",
    instructions=(
        "LightMES Manufacturing Execution System for notebook shell assembly. "
        "Use these tools to query production status, schedule work orders, "
        "and report defects. Most write operations require admin/supervisor role."
    ),
)


def mount_mcp(app) -> None:
    """Mount MCP server onto FastAPI app at /mcp.
    Implemented in Task 2 (requires auth dependency first).
    """
    pass
```

创建空文件：
- `src/lightmes/modules/agent_gateway/auth.py`（占位，Task 2 填）
- `src/lightmes/modules/agent_gateway/errors.py`（占位，Task 2 填）
- `src/lightmes/modules/agent_gateway/schemas.py`（占位，Task 3 填）
- `src/lightmes/modules/agent_gateway/tools/__init__.py`（空）

`tests/modules/agent_gateway/__init__.py`（空）。

- [ ] **Step 3: 注册新模块到 main.py**

修改 `src/lightmes/main.py`：

```python
from lightmes.modules import api_v1, auth, integration, masterdata, production, trace, quality
```

添加 `agent_gateway`：

```python
from lightmes.modules import (
    agent_gateway, api_v1, auth, integration, masterdata, production, trace, quality,
)
```

在 `quality.register(app)` 之后、`api_v1.register(app)` 之前添加：

```python
agent_gateway.register(app)
```

注意：`api_v1.register(app)` 必须在 `agent_gateway.register(app)` 之前（API key 验证依赖先注册）。或反之。**当前 C 层 register 已经在 quality 后**，agent_gateway 应在 api_v1 之后：

```python
auth.register(app)
masterdata.register(app)
production.register(app)
trace.register(app)
integration.register(app)
quality.register(app)
api_v1.register(app)
agent_gateway.register(app)
```

- [ ] **Step 4: 验证 app 启动**

```bash
uv run python -c "from lightmes.main import app; print(app)"
```

应无报错。`/mcp` 暂未挂载（mount_mcp 是 stub）。

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock \
        src/lightmes/modules/agent_gateway/__init__.py \
        src/lightmes/modules/agent_gateway/server.py \
        src/lightmes/modules/agent_gateway/auth.py \
        src/lightmes/modules/agent_gateway/errors.py \
        src/lightmes/modules/agent_gateway/schemas.py \
        src/lightmes/modules/agent_gateway/tools/__init__.py \
        src/lightmes/main.py \
        tests/modules/agent_gateway/__init__.py
git commit -m "feat(agent-gateway): scaffold module + add fastmcp dependency"
```

---

### Task 2: Bearer auth + 错误处理 + mount_mcp 实现

**Files:**
- Modify: `src/lightmes/modules/agent_gateway/auth.py`（实现 verify_bearer 依赖 + _has_write_role helper）
- Modify: `src/lightmes/modules/agent_gateway/errors.py`（DomainError → MCP error 映射 handler）
- Modify: `src/lightmes/modules/agent_gateway/server.py`（实现 mount_mcp，挂载 FastMCP 到 /mcp）
- Modify: `src/lightmes/main.py`（lifespan 合并）
- Test: `tests/modules/agent_gateway/test_auth_and_mount.py`

**Interfaces:**
- Consumes: `ApiKeyService.validate`（来自 C 层）；`lightmes.shared.errors.DomainError`
- Produces:
  - `verify_bearer(authorization: str = Header(...)) -> User` FastAPI 依赖
  - `_has_write_role(user, db) -> bool` helper
  - `require_scope(scope)` 装饰器（用于 tool 函数，scope + role 双 gate）
  - `/mcp` endpoint 工作中（初始化握手、tools/list 返空数组）

- [ ] **Step 1: 写失败测试 - 验证 mount + auth**

创建 `tests/modules/agent_gateway/test_auth_and_mount.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _admin_with_key(db_session, username="mcpadmin"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="mcp-key", user_id=u.id, scopes=["read", "write"])
    return u, full_key


def test_mcp_endpoint_exists(client, db_session):
    """未带 auth → 401（FastAPI 依赖验证）。"""
    resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1.0"}},
    })
    # 未带 Authorization → 401
    assert resp.status_code == 401


def test_mcp_initialize_handshake(client, db_session):
    """完整 MCP initialize 握手。"""
    _, key = _admin_with_key(db_session)
    resp = client.post("/mcp", headers={"Authorization": f"Bearer {key}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1.0"}},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["protocolVersion"]
    assert data["result"]["serverInfo"]["name"] == "LightMES"


def test_mcp_invalid_bearer_returns_401(client, db_session):
    resp = client.post("/mcp", headers={"Authorization": "Bearer garbage"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    })
    assert resp.status_code == 401


def test_mcp_tools_list_empty_before_tools_registered(client, db_session):
    """Task 2 阶段：tools/list 返回空数组（tools 还没注册）。"""
    _, key = _admin_with_key(db_session)
    # 先 initialize
    init_resp = client.post("/mcp", headers={"Authorization": f"Bearer {key}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    })
    session_id = init_resp.headers.get("Mcp-Session-Id")
    # tools/list（Mcp-Session-Id header required by some servers）
    headers = {"Authorization": f"Bearer {key}"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    resp = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    # FastMCP 可能要求 initialized notification，简化断言：200 OK 且 result.tools 是数组
    assert resp.status_code == 200
    assert "result" in resp.json()
    assert isinstance(resp.json()["result"].get("tools", []), list)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/agent_gateway/test_auth_and_mount.py -v`
Expected: 失败（404 或 401 都可能，根据 mount_mcp 实现进度）。

- [ ] **Step 3: 实现 verify_bearer 依赖**

修改 `src/lightmes/modules/agent_gateway/auth.py`：

```python
from typing import Annotated
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.auth.models import User


_WRITE_REQUIRED_ROLES = {"admin", "supervisor"}


def _has_write_role(user: User, db: Session) -> bool:
    """Check user has admin/supervisor role (reuses C-layer pattern)."""
    full_user = db.execute(
        select(User).where(User.id == user.id).options(joinedload(User.role_obj))
    ).scalar_one_or_none()
    if full_user and full_user.role_obj:
        return full_user.role_obj.name in _WRITE_REQUIRED_ROLES
    legacy_role = getattr(user, "role", None)
    if legacy_role:
        return legacy_role in _WRITE_REQUIRED_ROLES
    return False


async def verify_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: validate Bearer lmk_xxx, inject user/api_key/db to request.state.

    Mounted via `app.mount("/mcp", mcp_app, dependencies=[Depends(verify_bearer)])`.
    """
    if not authorization or not authorization.startswith("Bearer lmk_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 Bearer lmk_xxx token",
        )
    full_key = authorization[len("Bearer "):]
    try:
        user, api_key = ApiKeyService(db).validate(full_key)
    except HTTPException:
        raise
    # 注入到 request.state 供 MCP tools 访问
    request.state.user = user
    request.state.api_key = api_key
    request.state.db_session = db
    return user
```

- [ ] **Step 4: 实现 mount_mcp + lifespan 合并**

修改 `src/lightmes/modules/agent_gateway/server.py`：

```python
from fastmcp import FastMCP

mcp = FastMCP(
    name="LightMES",
    instructions=(
        "LightMES Manufacturing Execution System for notebook shell assembly. "
        "Use these tools to query production status, schedule work orders, "
        "and report defects. Most write operations require admin/supervisor role."
    ),
)


def mount_mcp(app) -> None:
    """Mount MCP server onto FastAPI app at /mcp with Bearer auth dependency."""
    from lightmes.modules.agent_gateway.auth import verify_bearer

    # FastMCP HTTP app（ASGI）
    mcp_app = mcp.http_app(path="/mcp")

    # Mount with auth dependency（FastAPI mount 支持 dependencies 参数）
    app.mount("/mcp", mcp_app, dependencies=[Depends(verify_bearer)])

    # lifespan 合并：FastMCP session manager 需要在 app lifespan 中初始化
    from fastmcp.utilities.lifespan import combine_lifespans
    app.lifespan = combine_lifespans(app.lifespan, mcp_app.lifespan)
```

注意：
- `mcp.http_app(path="/mcp")` 返回 ASGI app，挂载到 `app.mount("/mcp", ...)` 时路径仍是 `/mcp`（path 参数告诉 FastMCP 内部路径）
- `dependencies=[Depends(verify_bearer)]` 在 mount 上加 → 所有 /mcp/* 请求都会经过 verify_bearer
- lifespan 合并通过 `combine_lifespans` helper（FastMCP 2.0+ 提供）

- [ ] **Step 5: 验证 lifespan 合并不破坏现有功能**

`uv run python -c "from lightmes.main import app; print('OK')"`

如果 `combine_lifespans` 不存在（版本差异），fallback：手动 wrap lifespan。简化做法（替换 server.py 中 lifespan 那行）：

```python
# Fallback: 不合并 lifespan，FastMCP 不要求 lifespan 也能工作（session 短连接）
# 省略 lifespan 合并行
```

如果 mount_mcp 不带 lifespan 合并也能工作，就删除 lifespan 合并行（保持简单）。

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/modules/agent_gateway/test_auth_and_mount.py -v`
Expected: 4 tests PASS。

如果 `test_mcp_initialize_handshake` 失败（lifespan 没启动），用 fallback 步骤 5 中的简化方案。

- [ ] **Step 7: 运行 C 层回归（不应破坏）**

Run: `uv run pytest tests/modules/api_v1/ tests/modules/agent_gateway/ -v`
Expected: 全部 PASS（C 层 + Task 2 的 4 个）。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/agent_gateway/auth.py \
        src/lightmes/modules/agent_gateway/server.py \
        tests/modules/agent_gateway/test_auth_and_mount.py
git commit -m "feat(agent-gateway): Bearer auth dependency + mount FastMCP at /mcp"
```

---

### Task 3: Schemas + Work Orders / Serial Units / Defects / API Keys thin wrappers（12 个）

**Files:**
- Modify: `src/lightmes/modules/agent_gateway/schemas.py`（Pydantic tool 输入输出）
- Create: `src/lightmes/modules/agent_gateway/tools/work_orders.py`（4 wrappers）
- Create: `src/lightmes/modules/agent_gateway/tools/serial_units.py`（3 wrappers）
- Create: `src/lightmes/modules/agent_gateway/tools/defects.py`（2 wrappers）
- Create: `src/lightmes/modules/agent_gateway/tools/api_keys.py`（3 wrappers）
- Modify: `src/lightmes/modules/agent_gateway/tools/__init__.py`（导入所有 tools 触发注册）
- Modify: `src/lightmes/modules/agent_gateway/auth.py`（加 require_scope 装饰器）
- Test: `tests/modules/agent_gateway/test_tools_wrappers.py`

**Interfaces:**
- Consumes: `lightmes.modules.api_v1.schemas`（复用 Read schemas）；service 层
- Produces: 12 thin wrapper MCP tools + require_scope decorator

- [ ] **Step 1: 写失败测试 - thin wrappers**

创建 `tests/modules/agent_gateway/test_tools_wrappers.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AGWP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AGWL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="AGWW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="AGWR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="AGWRR", name="r", pattern="AGW{SEQ:4}"))
    return p, line, r, rule


def _admin_with_key(db_session, username="wrapadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="wrap-key", user_id=u.id, scopes=["read", "write"])
    return u, full_key


def _mcp_call(client, key, method, params=None):
    """Helper: 单次 MCP 调用（不维持 session）。"""
    return client.post("/mcp", headers={"Authorization": f"Bearer {key}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {},
    })


def test_mcp_tools_list_contains_12_thin_wrappers(client, db_session):
    """tools/list 包含 12 个 thin wrapper 工具。"""
    _, key = _admin_with_key(db_session)
    # 先 initialize 拿 session
    init = _mcp_call(client, key, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    })
    session_id = init.headers.get("Mcp-Session-Id")
    headers = {"Authorization": f"Bearer {key}"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    # 发 initialized notification
    client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })
    # 列工具
    resp = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    expected = {
        "list_work_orders", "get_work_order",
        "create_work_order", "patch_work_order_priority",
        "list_serial_units", "get_serial_unit", "get_serial_unit_by_sn",
        "list_defects", "get_defect",
        "list_api_keys", "create_api_key", "revoke_api_key",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


def test_mcp_tool_list_work_orders(client, db_session):
    p, line, r, rule = _env(db_session)
    _, key = _admin_with_key(db_session)
    ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    resp = _mcp_call(client, key, "tools/call", {
        "name": "list_work_orders",
        "arguments": {"page": 1, "size": 20},
    })
    assert resp.status_code == 200
    # MCP tool 返回结构：result.content 是 list of {type: "text", text: "..."}
    result = resp.json()["result"]
    assert "content" in result
    # 解析 text 内容（FastMCP 默认返回 [TextContent]，text 是 JSON 序列化的结果）
    import json
    payload = json.loads(result["content"][0]["text"])
    assert isinstance(payload, list)
    assert any(wo["code"] == "AGWWO" for wo in payload)


def test_mcp_tool_get_serial_unit_by_sn(client, db_session):
    from lightmes.modules.production.models import SerialUnit
    p, line, r, rule = _env(db_session)
    _, key = _admin_with_key(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWSU", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AGWSN1", work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    resp = _mcp_call(client, key, "tools/call", {
        "name": "get_serial_unit_by_sn",
        "arguments": {"sn": "AGWSN1"},
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["sn"] == "AGWSN1"


def test_mcp_tool_create_work_order(client, db_session):
    p, line, r, rule = _env(db_session)
    _, key = _admin_with_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "create_work_order",
        "arguments": {"code": "AGWC1", "product_id": p.id, "routing_id": r.id,
                      "line_id": line.id, "qty": 50, "sn_rule_id": rule.id,
                      "priority": 7},
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["code"] == "AGWC1"
    assert payload["priority"] == 7


def test_mcp_tool_readonly_key_blocked_on_write(client, db_session):
    """read-only key 调写工具 → MCP error (permission denied)."""
    p, line, r, rule = _env(db_session)
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username="ro_u", password_hash=hash_password("p"),
             display_name="R", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    ro_key, _ = ApiKeyService(db_session).create(
        name="ro-key", user_id=u.id, scopes=["read"])
    resp = _mcp_call(client, ro_key, "tools/call", {
        "name": "create_work_order",
        "arguments": {"code": "AGWRO", "product_id": p.id, "routing_id": r.id,
                      "line_id": line.id, "qty": 1, "sn_rule_id": rule.id},
    })
    # FastMCP 错误返回结构：{"error": {"code": <int>, "message": "..."}}
    data = resp.json()
    assert "error" in data
    assert "scope" in data["error"]["message"].lower()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/agent_gateway/test_tools_wrappers.py -v`
Expected: 大量失败（工具未注册）。

- [ ] **Step 3: 实现 require_scope 装饰器**

修改 `src/lightmes/modules/agent_gateway/auth.py`，追加：

```python
import functools
from fastmcp.server.dependencies import get_http_request


def require_scope(scope: str):
    """Decorator for MCP tools. Checks API Key scope AND role (for write).

    MUST be applied AFTER @mcp.tool() decorator:
        @mcp.tool()
        @require_scope("read")
        def list_work_orders(...): ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            request = get_http_request()  # FastMCP 提供，返回当前 starlette Request
            if request is None:
                # 不在 HTTP 上下文（如本地直接调用），跳过 scope 检查
                return func(*args, **kwargs)
            api_key = getattr(request.state, "api_key", None)
            user = getattr(request.state, "user", None)
            db = getattr(request.state, "db_session", None)
            if api_key is None or user is None:
                raise PermissionError("未通过认证（无 API Key / User）")
            granted = set(api_key.scopes or [])
            if scope not in granted:
                raise PermissionError(f"API Key 缺少 scope: {scope}")
            if scope == "write" and not _has_write_role(user, db):
                raise PermissionError("写操作需要 admin/supervisor 角色")
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

**注意**：`get_http_request` 是 FastMCP 2.x 提供的 contextvar-based helper。如该 import 路径不存在（版本差异），用：

```python
from fastmcp.server.dependencies import get_http_headers
# 或
from starlette.requests import Request
# 用 ctx.request_context.request
```

如果 import 都失败，fallback：通过 `ctx: Context` 参数显式传递。但 `require_scope` 是装饰器，无法在签名上要求 ctx —— 必须用 contextvar。

实现提示：先尝试 `from fastmcp.server.dependencies import get_http_request`，失败时 grep fastmcp 包源码找正确路径。

- [ ] **Step 4: 实现 schemas**

修改 `src/lightmes/modules/agent_gateway/schemas.py`：

```python
"""Re-export C-layer schemas for tool input/output."""
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1,
    SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)

__all__ = [
    "ApiKeyCreate", "ApiKeyCreatedResponse", "ApiKeyRead",
    "DefectReadV1", "SerialUnitReadV1",
    "WorkOrderCreateV1", "WorkOrderPriorityPatch", "WorkOrderReadV1",
]
```

- [ ] **Step 5: 实现 work_orders wrappers**

创建 `src/lightmes/modules/agent_gateway/tools/work_orders.py`：

```python
"""Work Order MCP tools (thin wrappers over service layer)."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.server import mcp
from lightmes.modules.agent_gateway.schemas import (
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)


@mcp.tool()
@require_scope("read")
def list_work_orders(
    page: int = 1,
    size: int = 20,
    status: list[str] | None = None,
    line_id: int | None = None,
) -> list[WorkOrderReadV1]:
    """列出工单，分页 + 过滤（status/line_id）。"""
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select, func
    from lightmes.modules.production.models import WorkOrder

    db = get_http_request().state.db_session
    q = select(WorkOrder).order_by(WorkOrder.id.desc())
    if status:
        q = q.where(WorkOrder.status.in_(status))
    if line_id is not None:
        q = q.where(WorkOrder.line_id == line_id)
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    return [WorkOrderReadV1.model_validate(r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_work_order(work_order_id: int) -> WorkOrderReadV1:
    """按 id 查询工单。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.models import WorkOrder
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {work_order_id}")
    return WorkOrderReadV1.model_validate(wo)


@mcp.tool()
@require_scope("write")
def create_work_order(
    code: str,
    product_id: int,
    routing_id: int,
    line_id: int,
    qty: int,
    sn_rule_id: int | None = None,
    priority: int = 5,
) -> WorkOrderReadV1:
    """创建工单（write scope，需 admin/supervisor）。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate

    db = get_http_request().state.db_session
    wo = ProductionService(db).create_work_order(WorkOrderCreate(
        code=code, product_id=product_id, routing_id=routing_id,
        line_id=line_id, qty=qty, sn_rule_id=sn_rule_id))
    wo.priority = priority
    db.commit()
    db.refresh(wo)
    return WorkOrderReadV1.model_validate(wo)


@mcp.tool()
@require_scope("write")
def patch_work_order_priority(work_order_id: int, priority: int) -> WorkOrderReadV1:
    """调整工单优先级（1-9）。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.models import WorkOrder
    from lightmes.shared.errors import NotFoundError

    if not (1 <= priority <= 9):
        from lightmes.shared.errors import ValidationError
        raise ValidationError("priority 必须在 1-9 之间")
    db = get_http_request().state.db_session
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {work_order_id}")
    wo.priority = priority
    db.commit()
    db.refresh(wo)
    return WorkOrderReadV1.model_validate(wo)
```

- [ ] **Step 6: 实现 serial_units wrappers**

创建 `src/lightmes/modules/agent_gateway/tools/serial_units.py`：

```python
"""Serial Unit MCP tools."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.server import mcp
from lightmes.modules.agent_gateway.schemas import SerialUnitReadV1


@mcp.tool()
@require_scope("read")
def list_serial_units(
    work_order_id: int | None = None,
    status: list[str] | None = None,
    sn: str | None = None,
    page: int = 1,
    size: int = 20,
) -> list[SerialUnitReadV1]:
    """列出 SN 单元，可按 work_order_id/status/sn 过滤。"""
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select
    from lightmes.modules.production.models import SerialUnit

    db = get_http_request().state.db_session
    q = select(SerialUnit).order_by(SerialUnit.id.desc())
    if work_order_id is not None:
        q = q.where(SerialUnit.work_order_id == work_order_id)
    if status:
        q = q.where(SerialUnit.status.in_(status))
    if sn:
        q = q.where(SerialUnit.sn.ilike(f"%{sn}%"))
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    return [SerialUnitReadV1.model_validate(r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_serial_unit(serial_unit_id: int) -> SerialUnitReadV1:
    """按 id 查询 SN 单元。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.models import SerialUnit
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    su = db.get(SerialUnit, serial_unit_id)
    if su is None:
        raise NotFoundError(f"Serial unit 不存在: {serial_unit_id}")
    return SerialUnitReadV1.model_validate(su)


@mcp.tool()
@require_scope("read")
def get_serial_unit_by_sn(sn: str) -> SerialUnitReadV1:
    """按 SN 业务键查询。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    return SerialUnitReadV1.model_validate(su)
```

- [ ] **Step 7: 实现 defects wrappers**

创建 `src/lightmes/modules/agent_gateway/tools/defects.py`：

```python
"""Defect MCP tools."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.server import mcp
from lightmes.modules.agent_gateway.schemas import DefectReadV1


@mcp.tool()
@require_scope("read")
def list_defects(
    handling_status: list[str] | None = None,
    severity: list[str] | None = None,
    work_order_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> list[DefectReadV1]:
    """列出缺陷记录，可按 handling_status/severity/work_order_id 过滤。"""
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select
    from lightmes.modules.production.models import DefectRecord

    db = get_http_request().state.db_session
    q = select(DefectRecord).order_by(DefectRecord.id.desc())
    if handling_status:
        q = q.where(DefectRecord.handling_status.in_(handling_status))
    if severity:
        q = q.where(DefectRecord.severity.in_(severity))
    if work_order_id is not None:
        q = q.where(DefectRecord.work_order_id == work_order_id)
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    return [DefectReadV1.model_validate(r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_defect(defect_id: int) -> DefectReadV1:
    """按 id 查询缺陷记录。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.models import DefectRecord
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    d = db.get(DefectRecord, defect_id)
    if d is None:
        raise NotFoundError(f"缺陷不存在: {defect_id}")
    return DefectReadV1.model_validate(d)
```

- [ ] **Step 8: 实现 api_keys wrappers**

创建 `src/lightmes/modules/agent_gateway/tools/api_keys.py`：

```python
"""API Key MCP tools (admin only)."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.server import mcp
from lightmes.modules.agent_gateway.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
)


@mcp.tool()
@require_scope("read")
def list_api_keys() -> list[ApiKeyRead]:
    """列出当前用户的 API Keys（不含 key_hash 或 full_key）。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.api_v1.api_key_service import ApiKeyService

    db = get_http_request().state.db_session
    user = get_http_request().state.user
    keys = ApiKeyService(db).list_for_user(user.id)
    return [ApiKeyRead.model_validate(k) for k in keys]


@mcp.tool()
@require_scope("write")
def create_api_key(name: str, scopes: list[str] | None = None,
                   expires_at: str | None = None) -> ApiKeyCreatedResponse:
    """创建新 API Key。返回 full_key 一次。scopes 默认 ["read"]。"""
    from datetime import datetime
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.api_v1.api_key_service import ApiKeyService

    db = get_http_request().state.db_session
    user = get_http_request().state.user
    exp_dt = datetime.fromisoformat(expires_at) if expires_at else None
    full_key, record = ApiKeyService(db).create(
        name=name, user_id=user.id,
        scopes=scopes or ["read"], expires_at=exp_dt)
    db.commit()
    db.refresh(record)
    return ApiKeyCreatedResponse(
        id=record.id, name=record.name, key_prefix=record.key_prefix,
        scopes=record.scopes, full_key=full_key, created_at=record.created_at,
    )


@mcp.tool()
@require_scope("write")
def revoke_api_key(api_key_id: int) -> dict:
    """吊销 API Key（仅可吊销自己的）。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    from lightmes.modules.auth.models import ApiKey
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    user = get_http_request().state.user
    target = db.get(ApiKey, api_key_id)
    if target is None or target.user_id != user.id:
        raise NotFoundError(f"API Key 不存在: {api_key_id}")
    ApiKeyService(db).revoke(api_key_id, revoked_by_user_id=user.id)
    db.commit()
    return {"ok": True}
```

- [ ] **Step 9: 注册所有 tools**

修改 `src/lightmes/modules/agent_gateway/tools/__init__.py`：

```python
"""All MCP tools. Import triggers registration on `mcp` instance."""
from lightmes.modules.agent_gateway.tools import (  # noqa: F401
    api_keys, defects, serial_units, work_orders,
)
```

修改 `src/lightmes/modules/agent_gateway/server.py` 的 `mount_mcp`，在 `mcp.http_app()` 之前导入 tools：

```python
def mount_mcp(app) -> None:
    """Mount MCP server onto FastAPI app at /mcp with Bearer auth dependency."""
    from lightmes.modules.agent_gateway.auth import verify_bearer
    # 触发 tool 注册
    from lightmes.modules.agent_gateway.tools import (  # noqa: F401
        api_keys, defects, serial_units, work_orders,
    )

    mcp_app = mcp.http_app(path="/mcp")
    app.mount("/mcp", mcp_app, dependencies=[Depends(verify_bearer)])
```

- [ ] **Step 10: 运行测试**

Run: `uv run pytest tests/modules/agent_gateway/test_tools_wrappers.py -v`
Expected: 5 tests PASS。

如果 `get_http_request` import 失败：
- 查 fastmcp 实际 API：`uv run python -c "import fastmcp; print(dir(fastrmcp.server.dependencies))"`
- 备选：用 `from fastmcp import Context` 然后在 tool 签名加 `ctx: Context` 参数

- [ ] **Step 11: Commit**

```bash
git add src/lightmes/modules/agent_gateway/schemas.py \
        src/lightmes/modules/agent_gateway/auth.py \
        src/lightmes/modules/agent_gateway/server.py \
        src/lightmes/modules/agent_gateway/tools/__init__.py \
        src/lightmes/modules/agent_gateway/tools/work_orders.py \
        src/lightmes/modules/agent_gateway/tools/serial_units.py \
        src/lightmes/modules/agent_gateway/tools/defects.py \
        src/lightmes/modules/agent_gateway/tools/api_keys.py \
        tests/modules/agent_gateway/test_tools_wrappers.py
git commit -m "feat(agent-gateway): 12 thin wrapper MCP tools + require_scope decorator"
```

---

### Task 4: C 层补 list_defect_types + 1 个 thin wrapper

**Files:**
- Modify: `src/lightmes/modules/api_v1/router.py`（追加 GET /api/v1/defect-types）
- Modify: `src/lightmes/modules/api_v1/schemas.py`（DefectTypeReadV1）
- Create: `src/lightmes/modules/agent_gateway/tools/defect_types.py`
- Modify: `src/lightmes/modules/agent_gateway/tools/__init__.py`
- Test: `tests/modules/api_v1/test_defect_types_endpoints.py`
- Test: `tests/modules/agent_gateway/test_tools_wrappers.py`（扩展）

**Interfaces:**
- Consumes: `DefectType` model
- Produces:
  - C 层端点 `GET /api/v1/defect-types`（用于让 Agent 列出可用 defect_type_code）
  - MCP tool `list_defect_types`

- [ ] **Step 1: 写失败测试 - C 层端点**

创建 `tests/modules/api_v1/test_defect_types_endpoints.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.production.models import DefectType
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _key(db_session):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username="dtadm", password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="dt-key", user_id=u.id, scopes=["read"])
    return full_key


def test_defect_types_list(client, db_session):
    db_session.add(DefectType(code="SCRATCH", name="刮花", category="外观",
                              severity="minor", is_active=True))
    db_session.add(DefectType(code="CRACK", name="裂纹", category="外观",
                              severity="critical", is_active=True))
    db_session.flush()
    key = _key(db_session)
    resp = client.get("/api/v1/defect-types",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    data = resp.json()
    codes = {d["code"] for d in data}
    assert "SCRATCH" in codes
    assert "CRACK" in codes


def test_defect_types_list_filter_active(client, db_session):
    db_session.add(DefectType(code="ACTIVE1", name="A", category="外观",
                              severity="minor", is_active=True))
    db_session.add(DefectType(code="INACTIVE1", name="I", category="外观",
                              severity="minor", is_active=False))
    db_session.flush()
    key = _key(db_session)
    resp = client.get("/api/v1/defect-types?is_active=true",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    codes = {d["code"] for d in resp.json()}
    assert "ACTIVE1" in codes
    assert "INACTIVE1" not in codes
```

- [ ] **Step 2: 实现 C 层端点 + schema**

修改 `src/lightmes/modules/api_v1/schemas.py`，追加：

```python
class DefectTypeReadV1(BaseModel):
    """Defect type for API v1."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    category: str | None
    severity: str
    description: str | None
    is_active: bool
```

修改 `src/lightmes/modules/api_v1/router.py`，追加：

```python
# ---- Defect Types ----

_DT_TAG = "Defect Types"


@router.get("/defect-types", response_model=list[DefectTypeReadV1], tags=[_DT_TAG])
def list_defect_types(
    response: Response,
    is_active: bool | None = Query(default=None),
    category: list[str] = Query(default=[], max_length=20),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[DefectTypeReadV1]:
    """列出缺陷类型。可按 is_active / category 过滤。"""
    from sqlalchemy import select, func
    from lightmes.modules.production.models import DefectType
    q = select(DefectType).order_by(DefectType.id.desc())
    if is_active is not None:
        q = q.where(DefectType.is_active == is_active)
    if category:
        q = q.where(DefectType.category.in_(category))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q).scalars().all())  # 无分页（数量小）
    response.headers["X-Total-Count"] = str(total)
    return [DefectTypeReadV1.model_validate(r) for r in rows]
```

`DefectTypeReadV1` 加入 schemas import：

```python
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1, DefectTypeReadV1, SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/modules/api_v1/test_defect_types_endpoints.py -v`
Expected: 2 PASS。

- [ ] **Step 4: 实现 MCP thin wrapper**

创建 `src/lightmes/modules/agent_gateway/tools/defect_types.py`：

```python
"""Defect type MCP tool."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.server import mcp
from lightmes.modules.agent_gateway.schemas import DefectTypeReadV1


@mcp.tool()
@require_scope("read")
def list_defect_types(
    is_active: bool | None = True,
) -> list[DefectTypeReadV1]:
    """列出缺陷类型（默认仅 active）。用于让 Agent 知道可用的 defect_type_code。"""
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select
    from lightmes.modules.production.models import DefectType

    db = get_http_request().state.db_session
    q = select(DefectType).order_by(DefectType.id)
    if is_active is not None:
        q = q.where(DefectType.is_active == is_active)
    rows = list(db.execute(q).scalars().all())
    return [DefectTypeReadV1.model_validate(r) for r in rows]
```

schemas.py 追加 import：

```python
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1, DefectTypeReadV1, SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
```

修改 `src/lightmes/modules/agent_gateway/tools/__init__.py`：

```python
from lightmes.modules.agent_gateway.tools import (  # noqa: F401
    api_keys, defect_types, defects, serial_units, work_orders,
)
```

修改 `src/lightmes/modules/agent_gateway/server.py` 的 mount_mcp，导入也加 `defect_types`：

```python
    from lightmes.modules.agent_gateway.tools import (  # noqa: F401
        api_keys, defect_types, defects, serial_units, work_orders,
    )
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/modules/agent_gateway/test_tools_wrappers.py tests/modules/api_v1/test_defect_types_endpoints.py -v`
Expected: 全部 PASS。

可选：扩展 `test_mcp_tools_list_contains_12_thin_wrappers` 改名 `test_mcp_tools_list_contains_13_thin_wrappers`，添加 `list_defect_types` 到 expected 集合。

- [ ] **Step 6: Commit**

```bash
git add src/lightmes/modules/api_v1/router.py \
        src/lightmes/modules/api_v1/schemas.py \
        src/lightmes/modules/agent_gateway/tools/defect_types.py \
        src/lightmes/modules/agent_gateway/tools/__init__.py \
        src/lightmes/modules/agent_gateway/server.py \
        src/lightmes/modules/agent_gateway/schemas.py \
        tests/modules/api_v1/test_defect_types_endpoints.py
git commit -m "feat(agent-gateway): C-layer /defect-types + MCP thin wrapper"
```

---

### Task 5: `query_production_status` compose 工具

**Files:**
- Create: `src/lightmes/modules/agent_gateway/tools/query.py`
- Modify: `src/lightmes/modules/agent_gateway/tools/__init__.py`
- Modify: `src/lightmes/modules/agent_gateway/schemas.py`（追加 ProductionStatusResult）
- Test: `tests/modules/agent_gateway/test_compose_query.py`

**Interfaces:**
- Consumes: `WorkOrder`, `DefectRecord`, `Line`, `SerialUnitRepository`
- Produces: `query_production_status` MCP tool

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/agent_gateway/test_compose_query.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import SerialUnit, DefectType, DefectRecord
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env_with_progress(db_session, sn="AGWQS1"):
    """Create WO + 1 SN with defects for compose query testing."""
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AGWQSP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AGWQSL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="AGWQSW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="AGWQSR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="AGWQSRR", name="r", pattern="AGWQS{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWQSWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn=sn, work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=2)
    db_session.add(su); db_session.flush()
    wo.produced_qty = 5  # 50% 进度
    db_session.flush()
    # 加 2 条缺陷
    dt = DefectType(code="QSCRATCH", name="刮花", category="外观",
                    severity="minor", is_active=True)
    db_session.add(dt); db_session.flush()
    for i in range(2):
        db_session.add(DefectRecord(
            defect_type_id=dt.id, defect_type_code=dt.code, defect_type_name=dt.name,
            severity=dt.severity, serial_unit_id=su.id, work_order_id=wo.id,
            operation_id=None, work_station_id=None, position=None,
            discovered_by=None, handling_status="pending"))
    db_session.flush()
    return wo, su


def _admin_key(db_session, username="qadm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="q-key", user_id=u.id, scopes=["read"])
    return full_key


def _mcp_call(client, key, method, params=None):
    return client.post("/mcp", headers={"Authorization": f"Bearer {key}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {},
    })


def test_query_production_status_by_wo_code(client, db_session):
    wo, su = _env_with_progress(db_session)
    key = _admin_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "query_production_status",
        "arguments": {"work_order_code": "AGWQSWO"},
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["work_order"]["code"] == "AGWQSWO"
    assert payload["produced_qty"] == 5
    assert payload["planned_qty"] == 10
    assert payload["progress_percent"] == 50
    assert len(payload["recent_defects"]) == 2
    assert payload["line"]["code"] == "AGWQSL"


def test_query_production_status_by_sn(client, db_session):
    wo, su = _env_with_progress(db_session, sn="AGWQSN1")
    key = _admin_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "query_production_status",
        "arguments": {"sn": "AGWQSN1"},
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["work_order"]["code"] == "AGWQSWO"


def test_query_production_status_not_found(client, db_session):
    key = _admin_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "query_production_status",
        "arguments": {"work_order_code": "NOSUCH"},
    })
    # DomainError → MCP error
    data = resp.json()
    assert "error" in data
    assert "不存在" in data["error"]["message"] or "工单" in data["error"]["message"]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/agent_gateway/test_compose_query.py -v`
Expected: 失败（工具未注册）。

- [ ] **Step 3: 实现 ProductionStatusResult schema**

修改 `src/lightmes/modules/agent_gateway/schemas.py`，追加：

```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ProductionStatusResult(BaseModel):
    """query_production_status 工具的返回结构。"""
    work_order: WorkOrderReadV1
    produced_qty: int
    planned_qty: int
    progress_percent: int
    recent_defects: list[DefectReadV1]
    is_overdue: bool
    line: dict | None  # {"id", "code", "name"}
    serial_unit: SerialUnitReadV1 | None = None  # 若按 sn 查询，附带 SN 信息
```

- [ ] **Step 4: 实现 query_production_status 工具**

创建 `src/lightmes/modules/agent_gateway/tools/query.py`：

```python
"""Compose tool: query_production_status."""
from datetime import datetime
from sqlalchemy import select
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    DefectReadV1, ProductionStatusResult, SerialUnitReadV1, WorkOrderReadV1,
)
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def query_production_status(
    work_order_code: str | None = None,
    sn: str | None = None,
    work_order_id: int | None = None,
) -> ProductionStatusResult:
    """查询生产状态：工单进度、最近缺陷、产线、超期状态。

    三种识别方式任选一：work_order_code、sn、work_order_id。
    返回综合视图，无需 Agent 手工 compose 多个 API 调用。
    """
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.models import WorkOrder, DefectRecord
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.modules.masterdata.models import Line
    from lightmes.shared.errors import NotFoundError, ValidationError

    if not any([work_order_code, sn, work_order_id]):
        raise ValidationError("必须提供 work_order_code / sn / work_order_id 之一")

    db = get_http_request().state.db_session

    # 1. 解析 WO
    wo = None
    su = None
    if work_order_id is not None:
        wo = db.get(WorkOrder, work_order_id)
    elif work_order_code is not None:
        wo = db.execute(
            select(WorkOrder).where(WorkOrder.code == work_order_code)
        ).scalar_one_or_none()
    elif sn is not None:
        su = SerialUnitRepository(db).get_by_sn(sn)
        if su is not None:
            wo = db.get(WorkOrder, su.work_order_id)
    if wo is None:
        raise NotFoundError(
            f"工单不存在: code={work_order_code} sn={sn} id={work_order_id}")

    # 2. 聚合进度
    produced = wo.produced_qty or 0
    planned = wo.qty or 0
    progress = int(produced * 100 / planned) if planned > 0 else 0

    # 3. 最近 5 条缺陷
    recent_defects = list(db.execute(
        select(DefectRecord)
        .where(DefectRecord.work_order_id == wo.id)
        .order_by(DefectRecord.id.desc())
        .limit(5)
    ).scalars().all())

    # 4. 产线
    line = None
    if wo.line_id is not None:
        line_obj = db.get(Line, wo.line_id)
        if line_obj is not None:
            line = {"id": line_obj.id, "code": line_obj.code, "name": line_obj.name}

    # 5. 超期判定
    is_overdue = (
        wo.planned_end is not None
        and wo.planned_end < datetime.now()
        and produced < planned
    )

    return ProductionStatusResult(
        work_order=WorkOrderReadV1.model_validate(wo),
        produced_qty=produced,
        planned_qty=planned,
        progress_percent=progress,
        recent_defects=[DefectReadV1.model_validate(d) for d in recent_defects],
        is_overdue=is_overdue,
        line=line,
        serial_unit=SerialUnitReadV1.model_validate(su) if su else None,
    )
```

- [ ] **Step 5: 注册 query 模块**

修改 `src/lightmes/modules/agent_gateway/tools/__init__.py`：

```python
from lightmes.modules.agent_gateway.tools import (  # noqa: F401
    api_keys, defect_types, defects, query, serial_units, work_orders,
)
```

修改 `src/lightmes/modules/agent_gateway/server.py` 的 mount_mcp：

```python
    from lightmes.modules.agent_gateway.tools import (  # noqa: F401
        api_keys, defect_types, defects, query, serial_units, work_orders,
    )
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/modules/agent_gateway/test_compose_query.py -v`
Expected: 3 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/lightmes/modules/agent_gateway/tools/query.py \
        src/lightmes/modules/agent_gateway/tools/__init__.py \
        src/lightmes/modules/agent_gateway/server.py \
        src/lightmes/modules/agent_gateway/schemas.py \
        tests/modules/agent_gateway/test_compose_query.py
git commit -m "feat(agent-gateway): query_production_status compose tool"
```

---

### Task 6: list_backlog + create_and_schedule + report_defect 工具

**Files:**
- Create: `src/lightmes/modules/agent_gateway/tools/planner.py`（list_backlog, create_and_schedule_work_order）
- Modify: `src/lightmes/modules/agent_gateway/tools/defects.py`（追加 report_defect_for_sn）
- Modify: `src/lightmes/modules/agent_gateway/schemas.py`（追加 BacklogItem, CreateAndScheduleResult, ReportDefectResult）
- Modify: `src/lightmes/modules/agent_gateway/tools/__init__.py`
- Test: `tests/modules/agent_gateway/test_compose_more.py`

**Interfaces:**
- Consumes: `PlannerService`, `ProductionService`, `DefectService`, `DefectType`, `Product`, `Line`, `Routing`
- Produces: 3 compose 工具

- [ ] **Step 1: 写失败测试**

创建 `tests/modules/agent_gateway/test_compose_more.py`：

```python
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.models import SerialUnit, DefectType
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _env(db_session):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AGWMP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AGWML", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="AGWMW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="AGWMR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(
        SnRuleCreate(code="AGWMRR", name="r", pattern="AGWM{SEQ:4}"))
    return p, line, r, rule


def _admin_key(db_session, username="madm"):
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="m-key", user_id=u.id, scopes=["read", "write"])
    return full_key


def _mcp_call(client, key, method, params=None):
    return client.post("/mcp", headers={"Authorization": f"Bearer {key}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {},
    })


def test_list_backlog(client, db_session):
    p, line, r, rule = _env(db_session)
    ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWMWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    key = _admin_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "list_backlog", "arguments": {},
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert any(b["code"] == "AGWMWO" for b in payload["backlog"])


def test_create_and_schedule_work_order(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "create_and_schedule_work_order",
        "arguments": {
            "product_code": "AGWMP", "qty": 50, "line_code": "AGWML",
            "planned_start": "2026-08-20T08:00:00",
            "planned_end": "2026-08-20T16:00:00",
            "priority": 7,
        },
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["scheduled"] is True
    assert payload["work_order"]["code"]  # 自动生成或自定义


def test_create_and_schedule_conflict(client, db_session):
    p, line, r, rule = _env(db_session)
    key = _admin_key(db_session)
    # 第一次成功
    _mcp_call(client, key, "tools/call", {
        "name": "create_and_schedule_work_order",
        "arguments": {
            "product_code": "AGWMP", "qty": 50, "line_code": "AGWML",
            "planned_start": "2026-08-20T08:00:00",
            "planned_end": "2026-08-20T16:00:00",
        },
    })
    # 第二次同时段冲突
    resp = _mcp_call(client, key, "tools/call", {
        "name": "create_and_schedule_work_order",
        "arguments": {
            "product_code": "AGWMP", "qty": 30, "line_code": "AGWML",
            "planned_start": "2026-08-20T10:00:00",
            "planned_end": "2026-08-20T18:00:00",
        },
    })
    data = resp.json()
    assert "error" in data
    assert "占用" in data["error"]["message"] or "冲突" in data["error"]["message"]


def test_report_defect_for_sn(client, db_session):
    p, line, r, rule = _env(db_session)
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AGWMDF", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AGWMDF1", work_order_id=wo.id, product_id=p.id,
                    status="in_process", current_operation_seq=1)
    db_session.add(su); db_session.flush()
    dt = DefectType(code="AGWMSCRATCH", name="刮花", category="外观",
                    severity="minor", is_active=True)
    db_session.add(dt); db_session.flush()
    key = _admin_key(db_session)
    resp = _mcp_call(client, key, "tools/call", {
        "name": "report_defect_for_sn",
        "arguments": {
            "sn": "AGWMDF1",
            "defect_type_code": "AGWMSCRATCH",
            "remark": "外壳刮花 2cm",
        },
    })
    assert resp.status_code == 200
    import json
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["defect_record"]["defect_type_code"] == "AGWMSCRATCH"
    assert payload["serial_unit_status"] == "quarantined"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/modules/agent_gateway/test_compose_more.py -v`
Expected: 失败。

- [ ] **Step 3: 实现 schemas**

修改 `src/lightmes/modules/agent_gateway/schemas.py`，追加：

```python
class BacklogItem(BaseModel):
    """list_backlog 返回的单条 item。"""
    id: int
    code: str
    priority: int
    qty: int
    product_code: str | None
    product_name: str | None


class BacklogResult(BaseModel):
    backlog: list[BacklogItem]
    total: int


class CreateAndScheduleResult(BaseModel):
    work_order: WorkOrderReadV1
    scheduled: bool
    conflict: dict | None = None  # {"work_order_id", "work_order_code"}


class ReportDefectResult(BaseModel):
    defect_record: DefectReadV1
    serial_unit_status: str
```

- [ ] **Step 4: 实现 planner.py（list_backlog + create_and_schedule）**

创建 `src/lightmes/modules/agent_gateway/tools/planner.py`：

```python
"""Compose tools: list_backlog, create_and_schedule_work_order."""
from datetime import datetime
from sqlalchemy import select
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    BacklogItem, BacklogResult, CreateAndScheduleResult, WorkOrderReadV1,
)
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_backlog(line_id: int | None = None) -> BacklogResult:
    """列出未排程工单（planned_start IS NULL）。按 priority desc 排序。"""
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.modules.masterdata.query_service import MasterDataQueryService

    db = get_http_request().state.db_session
    wos = PlannerService(db).list_backlog(line_id=line_id)
    query = MasterDataQueryService(db)
    items = []
    for wo in wos:
        p = query.get_product(wo.product_id)
        items.append(BacklogItem(
            id=wo.id, code=wo.code, priority=wo.priority, qty=wo.qty,
            product_code=p.code if p else None,
            product_name=p.name if p else None,
        ))
    return BacklogResult(backlog=items, total=len(items))


@mcp.tool()
@require_scope("write")
def create_and_schedule_work_order(
    product_code: str,
    qty: int,
    line_code: str,
    planned_start: str,  # ISO datetime
    planned_end: str,    # ISO datetime
    priority: int = 5,
    force_conflict: bool = False,
) -> CreateAndScheduleResult:
    """创建工单 + 排到产线时段（write scope，需 admin/supervisor）。

    自动解析 product_code/line_code，自动选 routing + sn_rule。
    若时段冲突，返回 conflict 信息（除非 force_conflict=True 且 supervisor）。
    """
    from fastmcp.server.dependencies import get_http_request
    from lightmes.modules.masterdata.models import Line
    from lightmes.modules.masterdata.repository import (
        ProductRepository, LineRepository, RoutingRepository,
    )
    from lightmes.modules.production.models import SnRule
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.modules.production.schemas import WorkOrderCreate
    from lightmes.shared.errors import NotFoundError, ValidationError

    db = get_http_request().state.db_session
    user = get_http_request().state.user

    # 解析 product
    product = ProductRepository(db).get_by_code(product_code)
    if product is None:
        raise NotFoundError(f"产品不存在: {product_code}")
    # 解析 line（按 code 查）
    line = next((l for l in LineRepository(db).list_all() if l.code == line_code), None)
    if line is None:
        raise NotFoundError(f"产线不存在: {line_code}")
    # 选 active routing
    routing = RoutingRepository(db).get_active_by_product(product.id)
    if routing is None:
        raise NotFoundError(f"产品无 active routing: {product_code}")
    # 选 sn_rule（用 product 关联的）
    sn_rules = list(db.execute(
        select(SnRule).where(SnRule.product_id == product.id)
    ).scalars().all())
    if not sn_rules:
        raise ValidationError(f"产品未配置 SN 规则: {product_code}")
    sn_rule_id = sn_rules[0].id

    # 解析时间
    try:
        start_dt = datetime.fromisoformat(planned_start)
        end_dt = datetime.fromisoformat(planned_end)
    except ValueError as e:
        raise ValidationError(f"时间格式错误（需 ISO 8601）: {e}")

    # 创建 WO
    wo = ProductionService(db).create_work_order(WorkOrderCreate(
        code=f"{product_code}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        product_id=product.id, routing_id=routing.id, line_id=line.id,
        qty=qty, sn_rule_id=sn_rule_id))
    wo.priority = priority
    db.flush()

    # 排程（冲突时不抛出，返回 conflict 信息让 Agent 决策）
    conflict = None
    try:
        PlannerService(db).schedule(
            wo.id, line.id, start_dt, end_dt,
            user_id=user.id, force=force_conflict)
    except Exception as e:
        conflict = {"error": str(getattr(e, "detail", e))}
    db.commit()
    db.refresh(wo)
    return CreateAndScheduleResult(
        work_order=WorkOrderReadV1.model_validate(wo),
        scheduled=conflict is None,
        conflict=conflict,
    )
```

- [ ] **Step 5: 实现 report_defect_for_sn**

修改 `src/lightmes/modules/agent_gateway/tools/defects.py`，追加：

```python
from lightmes.modules.agent_gateway.schemas import (
    DefectReadV1, ReportDefectResult,
)


@mcp.tool()
@require_scope("write")
def report_defect_for_sn(
    sn: str,
    defect_type_code: str,
    remark: str | None = None,
    position: str | None = None,
) -> ReportDefectResult:
    """按 SN 登记缺陷（write scope）。登记后 SN 自动隔离。

    defect_type_code 必须先用 list_defect_types 工具查询可用的 code。
    """
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select
    from lightmes.modules.production.defect_service import DefectService
    from lightmes.modules.production.models import DefectType
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    user = get_http_request().state.user

    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    dt = db.execute(
        select(DefectType).where(DefectType.code == defect_type_code)
    ).scalar_one_or_none()
    if dt is None or not dt.is_active:
        raise NotFoundError(f"缺陷类型不存在或已停用: {defect_type_code}")

    record = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=sn, discovered_by=user.id,
        operation_id=su.current_operation_seq,  # 近似用
        work_station_id=None, position=position, remark=remark)
    db.commit()
    db.refresh(record)
    db.refresh(su)
    return ReportDefectResult(
        defect_record=DefectReadV1.model_validate(record),
        serial_unit_status=su.status,
    )
```

- [ ] **Step 6: 注册 planner 模块**

修改 `src/lightmes/modules/agent_gateway/tools/__init__.py`：

```python
from lightmes.modules.agent_gateway.tools import (  # noqa: F401
    api_keys, defect_types, defects, planner, query, serial_units, work_orders,
)
```

修改 `src/lightmes/modules/agent_gateway/server.py` 的 mount_mcp：

```python
    from lightmes.modules.agent_gateway.tools import (  # noqa: F401
        api_keys, defect_types, defects, planner, query, serial_units, work_orders,
    )
```

- [ ] **Step 7: 运行测试**

Run: `uv run pytest tests/modules/agent_gateway/test_compose_more.py -v`
Expected: 4 PASS。

- [ ] **Step 8: Commit**

```bash
git add src/lightmes/modules/agent_gateway/tools/planner.py \
        src/lightmes/modules/agent_gateway/tools/defects.py \
        src/lightmes/modules/agent_gateway/schemas.py \
        src/lightmes/modules/agent_gateway/tools/__init__.py \
        src/lightmes/modules/agent_gateway/server.py \
        tests/modules/agent_gateway/test_compose_more.py
git commit -m "feat(agent-gateway): list_backlog + create_and_schedule + report_defect compose tools"
```

---

### Task 7: 回归 + OpenAPI tags + memory 更新

**Files:**
- Modify: `src/lightmes/main.py`（FastAPI description 追加 Agent Gateway 提示）
- Modify: `tests/modules/agent_gateway/test_full_catalog.py`（新建，全工具列表验证）
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\api_ecosystem.md`（追加 Agent Gateway 章节）
- Modify: `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\MEMORY.md`（追加索引行）

**Interfaces:**
- Consumes: 全部前 6 task
- Produces: 17 工具完整 + memory 文档

- [ ] **Step 1: 写全工具目录验证测试**

创建 `tests/modules/agent_gateway/test_full_catalog.py`：

```python
import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.shared.security import hash_password


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _key(db_session, scopes=None, username="catalogadm"):
    scopes = scopes or ["read", "write"]
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role is None:
        role = Role(name="admin", display_name="Admin")
        db_session.add(role); db_session.flush()
    u = User(username=username, password_hash=hash_password("p"),
             display_name="A", is_active=True, role_id=role.id)
    db_session.add(u); db_session.flush()
    full_key, _ = ApiKeyService(db_session).create(
        name="c-key", user_id=u.id, scopes=scopes)
    return full_key


def _mcp_call(client, key, method, params=None):
    return client.post("/mcp", headers={"Authorization": f"Bearer {key}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {},
    })


def test_mcp_catalog_has_17_tools(client, db_session):
    """tools/list 列出全部 17 个工具（13 thin + 4 compose）。"""
    key = _key(db_session)
    init = _mcp_call(client, key, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    })
    session_id = init.headers.get("Mcp-Session-Id")
    headers = {"Authorization": f"Bearer {key}"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })
    resp = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert len(tools) == 17
    tool_names = {t["name"] for t in tools}
    expected = {
        # 13 thin wrappers
        "list_work_orders", "get_work_order",
        "create_work_order", "patch_work_order_priority",
        "list_serial_units", "get_serial_unit", "get_serial_unit_by_sn",
        "list_defects", "get_defect",
        "list_defect_types",
        "list_api_keys", "create_api_key", "revoke_api_key",
        # 4 compose
        "query_production_status", "list_backlog",
        "create_and_schedule_work_order", "report_defect_for_sn",
    }
    assert tool_names == expected
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/modules/agent_gateway/test_full_catalog.py -v`
Expected: 1 PASS（17 工具完整）。

- [ ] **Step 3: 全套 agent_gateway + api_v1 回归**

Run: `uv run pytest tests/modules/agent_gateway/ tests/modules/api_v1/ -v`
Expected: 全部 PASS（~50+ tests，pre-existing dev-DB 失败 OUT OF SCOPE）。

- [ ] **Step 4: 更新 main.py description**

修改 `src/lightmes/main.py`，FastAPI 初始化中 description 字段追加：

```python
app = FastAPI(
    title=settings.app_name,
    version="0.3.0",  # 升级到 0.3.0（D 层 Agent Gateway 上线）
    description=(
        "LightMES — 轻量级制造执行系统（笔记本壳装配专线）。\n\n"
        "**API v1** (`/api/v1/*`)：JSON REST，为 ERP / BI / AI Agent 集成设计。"
        "Bearer token (`Authorization: Bearer lmk_live_xxx`)。\n\n"
        "**Agent Gateway** (`/mcp`)：MCP (Model Context Protocol) HTTP 端点，"
        "为 AI Agent (Claude Desktop / 自研 Agent) 提供 17 个工具。"
        "认证同 API v1 Bearer token。Agent 通过 `tools/list` 自动发现工具。\n\n"
        "**错误格式**：API v1 用 RFC 7807 Problem Details；MCP 用标准 JSON-RPC error。\n\n"
        "操作员 UI 见各模块 HTML 路由（不在本 OpenAPI 中）。"
    ),
    openapi_tags=[
        {"name": "Work Orders", "description": "工单 CRUD + 优先级"},
        {"name": "Serial Units", "description": "序列号单元查询"},
        {"name": "Defects", "description": "缺陷记录查询"},
        {"name": "Defect Types", "description": "缺陷类型字典"},
        {"name": "API Keys", "description": "API Key 管理（admin only）"},
    ],
)
```

- [ ] **Step 5: 更新 memory**

修改 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\api_ecosystem.md`，在文件末尾追加：

```markdown

## Agent Gateway（D 层 MCP，2026-08-12 上线）

**Why**: AI Agent (Claude Desktop / 自研) 需要任务导向接口，不是 C 层的资源导向 CRUD。

**How to apply**：
- 用 `fastmcp` 2.x（`from fastmcp import FastMCP`）创建 MCP server
- 挂载到 FastAPI：`app.mount("/mcp", mcp.http_app(path="/mcp"), dependencies=[Depends(verify_bearer)])`
- Bearer auth 依赖注入 `request.state.{user, api_key, db_session}`，tool 通过 `get_http_request().state` 访问
- 工具直接 import service 层（无 HTTP 循环）
- 17 工具：13 thin wrappers（1:1 对应 C 层）+ 4 compose（query_production_status / list_backlog / create_and_schedule_work_order / report_defect_for_sn）
- Scope gate：`@require_scope("read"|"write")` 装饰器（在 `@mcp.tool()` 之后）
- Write scope + admin/supervisor role 双 gate（与 C 层一致）
- DomainError → FastMCP 自动转 MCP error（JSON-RPC）

**Why not**：
- 不做 OpenAI Tools schema 导出（V1.1）
- 不做 MCP stdio transport（HTTP 够用）
- 不做 Webhook（独立 spec）

**关键文件**：
- `src/lightmes/modules/agent_gateway/server.py` — FastMCP 实例 + mount_mcp
- `src/lightmes/modules/agent_gateway/auth.py` — verify_bearer + require_scope
- `src/lightmes/modules/agent_gateway/tools/` — 所有 tool 模块

**手工验收**：用 MCP Inspector 连 `http://localhost:8000/mcp`，Bearer 用 admin 创建的 lmk_live_xxx，验证 tools/list 列出 17 工具。

[[api-ecosystem]]
```

修改 `C:\Users\zhaocao\.claude\projects\C--Users-zhaocao-Documents-GitHub-LightMES\memory\MEMORY.md`，追加一行（如还没有 agent-gateway 索引）：

```markdown
- [Agent Gateway](api_ecosystem.md#agent-gatewayd-层-mcp2026-08-12-上线) — D 层 MCP，17 工具供 AI Agent 调用
```

- [ ] **Step 6: Commit (不含 memory 文件)**

```bash
git add src/lightmes/main.py tests/modules/agent_gateway/test_full_catalog.py
git commit -m "test(agent-gateway): full catalog test (17 tools) + main.py description update"
```

memory 文件在仓库外，不进 git。

---

## 任务依赖

```
Task 1 (scaffold + dep)
  ↓
Task 2 (auth + mount)
  ↓
Task 3 (12 thin wrappers)
  ↓
Task 4 (list_defect_types C endpoint + wrapper)
  ↓
Task 5 (query_production_status compose)
  ↓
Task 6 (3 more compose)
  ↓
Task 7 (catalog test + memory)
```

顺序执行。

## 全套回归（任意 task 完成后均可运行）

```bash
uv run pytest tests/modules/agent_gateway/ tests/modules/api_v1/ -v
uv run python -c "from lightmes.main import app; print('OK')"
```

## 手工最终验收（Task 7 完成后）

```bash
uv run uvicorn lightmes.main:app --port 8000
```

用 MCP Inspector（`npx @modelcontextprotocol/inspector`）连 `http://localhost:8000/mcp`，Bearer 用 admin 创建的 `lmk_live_xxx`：
1. `tools/list` 列出 17 工具
2. 调 `query_production_status` 看返回结构
3. 调 `create_and_schedule_work_order` 看事务原子性
4. 用 read-only key 调写工具看 scope 拒绝
