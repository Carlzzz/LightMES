# Agent Gateway（D 层 MCP）- 设计文档

**日期**: 2026-08-12
**状态**: Approved
**关联**: API v1 Foundation（C 层）完成后，为 AI Agent 接入叠加任务导向 MCP 表面

---

## 1. 背景与目标

### 1.1 现状

API v1 Foundation（C 层）已于 2026-08-12 上线，提供 13 个 JSON REST 端点（work-orders / serial-units / defects / api-keys），支持 Bearer token + session 双路径认证、RFC 7807 Problem Details、ApiCallLog 审计。

C 层是**资源导向**（Resource Controller pattern），适合外部系统集成（ERP、BI）。但**AI Agent**需要更高层的**任务导向**工具，能一次调用完成"查询 WO 状态"、"创建+排程"、"按 SN 报告缺陷"等业务任务，而不是手工 compose 多个 REST 调用。

### 1.2 目标

构建 **D 层 Agent Gateway**：在 C 层之上叠加 **MCP (Model Context Protocol) HTTP 端点**，提供：

- **薄包装 + compose 混合工具目录**：13 个 1:1 thin wrappers（覆盖 C 层所有端点）+ 4 个任务导向 compose 工具
- **MCP Streamable HTTP transport**：单一 `/mcp` 端点，复用 Bearer token 认证
- **直接调 service 层**：不走 HTTP，避免循环调用、性能好 100x
- **完整 tool 发现**：Agent 通过 `tools/list` 自动获取所有工具的 JSON schema

### 1.3 非目标（明确不做）

- ❌ OpenAI Tools schema 导出（V1.1）
- ❌ MCP stdio transport（HTTP 够用）
- ❌ MCP resources（资源订阅概念）
- ❌ MCP prompts（预定义 prompt 模板）
- ❌ Webhook 事件推送（独立 spec）
- ❌ 长任务 / streaming 工具返回
- ❌ 工具版本化

---

## 2. 总体架构

```
Agent (Claude Desktop / 自研)
        ↓ MCP Streamable HTTP
        ↓
┌───────▼────────────────────────┐
│   /mcp 端点                    │
│   MCP server (FastMCP)         │
│   ─ Bearer auth (复用 API Key) │
│   ─ Session 管理（MCP 要求）   │
│   ─ Tool 调度                  │
└───────┬────────────────────────┘
        │ 直接 Python import
        ▼
┌────────────────────────────────┐
│   Service 层                   │
│   ProductionService            │
│   PlannerService               │
│   DefectService                │
│   ApiKeyService                │
└────────────────────────────────┘
        ▲
        │ HTTP (同 service 层)
┌───────┴────────────────────────┐
│   /api/v1/* (C 层 JSON API)    │
│   外部 ERP / BI / Agent (无 MCP)│
└────────────────────────────────┘
```

**关键设计决策**：MCP server **直接 import service 层**，不走 HTTP。理由：
- MCP server 和 LightMES 在同一进程，避免循环 HTTP 调用
- service 层已经处理 DomainError，MCP 层只做封装
- 性能：单进程内函数调用比 HTTP 快 100x
- 复用 service 层的事务边界

---

## 3. 认证模型

**复用 API Key Bearer**（与 C 层完全一致）：

- MCP 客户端在每次请求带 `Authorization: Bearer lmk_live_xxx`
- `BearerAuthMiddleware` 验证 key → 调 `ApiKeyService.validate()` → 得到 User
- 注入 `request.state.user`、`request.state.api_key`、`request.state.db_session`，供后续 tool 函数访问
- 后续 tool 调用都绑定到该 User（继承 role + scope 权限）
- 写操作（scope=write）仍需 admin/supervisor 角色（同 C 层 `require_api_key` 双 gate）

### URL 与生命周期

- **端点**：`POST /mcp`（MCP Streamable HTTP，单一端点）
- **传输**：HTTP + JSON response（FastMCP 的 `json_response=True`，比 SSE 简单）
- **Session**：MCP 协议层 session id（FastMCP 管理），与 LightMES session cookie 分开
- **握手流程**：客户端 send `initialize` → server 返回 capabilities → 客户端 send `initialized` notification → 后续 `tools/list`、`tools/call`

---

## 4. Tool 目录（17 个工具）

### 4.1 13 个 thin wrappers（1:1 转发 C 层）

每个工具的入参/返回 schema 完全对应 C 层端点。MCP 客户端通过 `tools/list` 自动发现。

| Tool | 对应 C 端点 | Scope |
|---|---|---|
| `list_work_orders` | GET /api/v1/work-orders | read |
| `get_work_order` | GET /api/v1/work-orders/{id} | read |
| `create_work_order` | POST /api/v1/work-orders | write |
| `patch_work_order_priority` | PATCH /api/v1/work-orders/{id}/priority | write |
| `list_serial_units` | GET /api/v1/serial-units | read |
| `get_serial_unit` | GET /api/v1/serial-units/{id} | read |
| `get_serial_unit_by_sn` | GET /api/v1/serial-units/by-sn/{sn} | read |
| `list_defects` | GET /api/v1/defects | read |
| `get_defect` | GET /api/v1/defects/{id} | read |
| `list_defect_types` | **新加**：GET /api/v1/defect-types（C 层补端点） | read |
| `list_api_keys` | GET /api/v1/api-keys | read |
| `create_api_key` | POST /api/v1/api-keys | write |
| `revoke_api_key` | DELETE /api/v1/api-keys/{id} | write |

注：tool 名用 snake_case，符合 MCP 命名约定。

### 4.2 4 个 compose 工具（任务导向）

#### `query_production_status`

**用途**：Agent 一句话问"WO-001 现在怎么样了？"或"SN X0001 到哪一步了？"

**入参**（任一）：
```python
{
  "work_order_code": "WO-001",  # 或
  "sn": "X0001",                # 或
  "work_order_id": 1
}
```

**返回**：
```python
{
  "work_order": { ... },
  "produced_qty": 50,
  "planned_qty": 100,
  "progress_percent": 50,
  "recent_defects": [...],       # 最近 5 条缺陷
  "is_overdue": false,
  "line": {"id": 1, "code": "L1", "name": "线1"}
}
```

**内部 compose 逻辑**：
- 输入解析（code/sn/id 三选一）
- 查 WorkOrder
- 聚合 produced_qty / planned_qty
- 查最近 5 条 DefectRecord（by work_order_id）
- 查关联 Line
- 计算是否超期

#### `list_backlog`

**用途**：Agent 看"还有哪些工单没排？"

**入参**：`{ "line_id": 1 }`（optional）

**返回**：
```python
{
  "backlog": [
    {"id": 1, "code": "WO-005", "priority": 8, "qty": 100, "product_code": "PXA"},
    ...
  ],
  "total": 5
}
```

**内部**：直接调 `PlannerService.list_backlog(line_id)`

#### `create_and_schedule_work_order`

**用途**：Agent 一次性创建工单 + 排到产线时段。

**入参**：
```python
{
  "product_code": "PXA",
  "qty": 100,
  "line_code": "L1",
  "planned_start": "2026-08-13T08:00:00",
  "planned_end": "2026-08-13T16:00:00",
  "priority": 7,
  "force_conflict": false
}
```

**返回**：
```python
{
  "work_order": { ... },
  "scheduled": true,
  "conflict": null  # 或 {"work_order_code": "WO-X", "work_order_id": 5}
}
```

**内部 compose 逻辑**：
- 查 Product by code → product_id
- 查 Line by code → line_id
- 查 active Routing by product → routing_id
- 查 SN rule for product（自动选）
- 调 `ProductionService.create_work_order(...)`
- 调 `PlannerService.schedule(wo.id, line_id, start, end, force=force_conflict)`
- 返回 WO + 排程状态

#### `report_defect_for_sn`

**用途**：操作员语音"X0001 这个外壳刮花"→ Agent 直接登记缺陷。

**入参**：
```python
{
  "sn": "X0001",
  "defect_type_code": "SCRATCH",
  "remark": "外壳刮花，约 2cm",
  "position": "顶盖"              # optional
}
```

**返回**：
```python
{
  "defect_record": { ... },
  "serial_unit_status": "quarantined"
}
```

**内部 compose 逻辑**：
- 查 SerialUnit by SN
- 查 DefectType by code（必须 active）
- 调 `DefectService.log_defect(...)`

### 4.3 工具发现（Agent 视角）

Agent 通过 MCP `tools/list` 调用看到全部 17 个工具的 schema + description。例如 `query_production_status`：

```json
{
  "name": "query_production_status",
  "description": "查询生产状态：工单进度、SN 当前工序、最近缺陷。三种识别方式任选一。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "work_order_code": {"type": "string"},
      "sn": {"type": "string"},
      "work_order_id": {"type": "integer"}
    }
  }
}
```

---

## 5. 实现细节

### 5.1 模块结构

```
src/lightmes/modules/agent_gateway/
├── __init__.py              # register(app) — 挂载 /mcp 到 FastAPI
├── server.py                # FastMCP 实例 + tool 注册
├── auth.py                  # BearerAuthMiddleware + require_scope decorator
├── errors.py                # DomainError → MCP error code 映射
├── schemas.py               # Pydantic 输入输出（agent-facing）
└── tools/
    ├── __init__.py
    ├── work_orders.py       # 4 thin wrappers
    ├── serial_units.py      # 3 thin wrappers
    ├── defects.py           # 2 thin wrappers + 1 compose
    ├── api_keys.py          # 3 thin wrappers
    ├── planner.py           # 1 compose (list_backlog)
    └── query.py             # 1 compose (query_production_status)
```

### 5.2 FastMCP 集成

```python
# src/lightmes/modules/agent_gateway/server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="LightMES",
    version="0.2.0",
    description="LightMES MES for notebook shell assembly — AI Agent 接入",
)


def register(app):
    """Mount MCP server onto FastAPI app at /mcp."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from lightmes.modules.agent_gateway.auth import BearerAuthMiddleware

    manager = StreamableHTTPSessionManager(app=mcp, json_response=True)
    app.mount("/mcp", BearerAuthMiddleware(manager))
```

### 5.3 Bearer Auth 中间件

```python
# src/lightmes/modules/agent_gateway/auth.py
class BearerAuthMiddleware:
    """验证 MCP 请求的 Bearer token，注入 User 到 request.state。"""

    def __init__(self, app):
        self.app = app  # StreamableHTTPSessionManager asgi app

    async def __call__(self, scope, receive, send):
        from starlette.requests import Request
        request = Request(scope, receive)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer lmk_"):
            response = JSONResponse(
                status_code=401,
                content={"error": "需要 Bearer lmk_xxx token"})
            await response(scope, receive, send)
            return
        db = SessionLocal()
        try:
            user, api_key = ApiKeyService(db).validate(auth[len("Bearer "):])
            scope["state"]["user"] = user
            scope["state"]["api_key"] = api_key
            scope["state"]["db_session"] = db
        except Exception as e:
            db.close()
            response = JSONResponse(
                status_code=401,
                content={"error": getattr(e, "detail", str(e))})
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            db.close()
```

### 5.4 Scope 检查

```python
def require_scope(scope: str):
    """Decorator for MCP tools. Checks API Key scope AND role (for write)."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, ctx: Context = None, **kwargs):
            request = ctx.request_context.request
            api_key = request.state.api_key
            user = request.state.user
            db = request.state.db_session

            granted = set(api_key.scopes or [])
            if scope not in granted:
                raise PermissionError(f"API Key 缺少 scope: {scope}")
            if scope == "write" and not _has_write_role(user, db):
                raise PermissionError("写操作需要 admin/supervisor 角色")

            return func(*args, ctx=ctx, **kwargs)
        return wrapper
    return decorator
```

### 5.5 错误映射

DomainError → MCP error response：

| 异常 | MCP error code | MCP error message |
|---|---|---|
| `ValidationError` | `invalid_params` (-32602) | detail 直接传 |
| `NotFoundError` | `invalid_params` (-32602) | detail 直接传 |
| `ConflictError` | `invalid_params` (-32602) | detail 直接传（含冲突 WO 信息） |
| `BusinessRuleError` | `invalid_params` (-32602) | detail 直接传 |
| Scope 不足 | `permission_denied` (-32603) | "API Key 缺少 scope: write" |
| 未登录 | `permission_denied` (-32603) | "需要 Bearer token" |
| 其他 `Exception` | `internal_error` (-32603) | "服务端错误，联系管理员（trace_id: xxx）" |

### 5.6 Tool 函数示例

```python
from mcp.server.fastmcp import Context
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import WorkOrderRead

@mcp.tool()
@require_scope("read")
def list_work_orders(
    page: int = 1,
    size: int = 20,
    ctx: Context = None,
) -> list[WorkOrderRead]:
    """列出工单，分页 + 过滤。"""
    db = ctx.request_context.request.state.db_session
    from sqlalchemy import select, func
    from lightmes.modules.production.models import WorkOrder
    q = select(WorkOrder).order_by(WorkOrder.id.desc())
    # ... 查询逻辑（同 C 层）
    return [WorkOrderRead.model_validate(r) for r in rows]
```

---

## 6. 测试策略

| 测试 | 覆盖点 |
|---|---|
| `test_mcp_initialize_handshake` | initialize → capabilities → initialized |
| `test_mcp_tools_list_returns_all_17_tools` | tools/list 返回完整 tool 目录 |
| `test_mcp_tool_call_list_work_orders` | 简单工具调用 |
| `test_mcp_tool_call_query_production_status_by_sn` | compose 工具 sn 路径 |
| `test_mcp_tool_call_query_production_status_by_wo_code` | compose 工具 code 路径 |
| `test_mcp_tool_call_create_and_schedule_work_order_success` | compose 写路径 |
| `test_mcp_tool_call_create_and_schedule_conflict` | 冲突 → ConflictError → MCP error |
| `test_mcp_tool_call_report_defect_for_sn` | defect compose |
| `test_mcp_auth_missing_bearer_returns_401` | auth 失败 |
| `test_mcp_auth_invalid_bearer_returns_401` | auth 失败 |
| `test_mcp_auth_readonly_key_blocked_on_write_tool` | scope 校验 |
| `test_mcp_auth_operator_blocked_on_write_tool` | role gating |
| `test_mcp_domain_error_maps_to_invalid_params` | 错误映射 |

**测试方法**：
- 直接调 `mcp.call_tool(name, args)` 测试工具逻辑（不通过 HTTP，简化）
- TestClient + /mcp 端点测试 auth + handshake（端到端）
- DomainError → MCP error 映射单独单测
- 不需要测 MCP SDK 本身（开源库已测）

---

## 7. 任务拆分（预估 7 task）

1. **依赖 + 模块结构** — 加 `mcp` 依赖；创建 `agent_gateway/` 目录骨架；`register(app)` stub 挂载到 `/mcp`
2. **Bearer auth + 错误处理** — middleware + scope/role 检查装饰器 + DomainError 映射
3. **13 thin wrapper 工具** — work-orders / serial-units / defects / api-keys（直接调 service 层）
4. **新增 `list_defect_types` C 层端点 + thin wrapper** — C 层补端点（agent_gateway 依赖）
5. **`query_production_status` compose 工具**
6. **`list_backlog` + `create_and_schedule_work_order` + `report_defect_for_sn` compose 工具**
7. **回归 + memory 更新**

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `mcp` SDK 是 RC（非 stable） | 锁定 `mcp>=1.17,<2.0`；接受 RC 状态（功能已完整） |
| FastMCP `Context` 与 FastAPI `Request` 集成方式可能变 | 中间件注入 `request.state` 模式，FastMCP 升级时只需改一行 |
| MCP 单 HTTP 端点 + session 管理，与 ApiCallLog middleware 不直接兼容 | 在 BearerAuthMiddleware 内部直接写 ApiCallLog（独立 session） |
| Scope 检查装饰器与 FastMCP tool decorator 顺序可能冲突 | 测试覆盖：每个写工具都验证 scope 检查生效 |
| 多个 compose 工具内部调多个 service，事务边界模糊 | service 层不变（各自 flush 不 commit）；compose tool 顶层统一 commit |
| Agent 误调用写工具 | scope + role 双重 gate；ApiCallLog 记录所有写工具调用（审计可追溯） |

---

## 9. 手工验收（Task 7 完成后）

```bash
uv run uvicorn lightmes.main:app --port 8000
```

用 MCP Inspector（`npx @modelcontextprotocol/inspector`）连 `http://localhost:8000/mcp`，Bearer 用 admin 创建的 `lmk_live_xxx`，验证：
- `tools/list` 列出 17 个工具
- 调 `query_production_status` 看返回结构
- 调 `create_and_schedule_work_order` 看事务原子性
- 用 read-only key 调写工具看 scope 拒绝

---

## 10. 不在本 spec（V1.1+）

- OpenAI Tools schema 导出
- MCP stdio transport
- MCP resources（资源订阅）
- MCP prompts（预定义 prompt 模板）
- Webhook 事件推送
- 工具调用结果 streaming（如长查询分块返回）
- 工具版本化（未来 breaking change 处理）
