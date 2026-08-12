# API v1 基础层（AI Agent 接入准备）- 设计文档

**日期**: 2026-08-12
**状态**: Approved
**关联**: 为 AI Agent 接入（D 层 Agent Gateway）打基础；借鉴 OpenMES 的 Resource Controller + Sanctum 风格 Token Auth

---

## 1. 背景与目标

### 1.1 现状

LightMES 当前 77 个路由，绝大多数是 HTMX HTML 片段（操作员 UI）。只有 ~13 个 JSON API（masterdata CRUD 8 + integration import 4 + planner changes 1）。Auth 全部走 session cookie。

这套架构对**人类操作员**友好，但**外部系统/AI Agent 接入困难**：
- 无 Bearer token，只有 cookie session
- 无版本化 `/api/v1/`，未来 breaking change 难处理
- 无统一错误格式（DomainError 直接 400）
- 无审计日志（Agent 报错难追溯）
- 资源模型不一致（HTML 路由 vs JSON 路由）

### 1.2 目标

构建 **C 层**：在现有 service 层之上叠加一条 **`/api/v1/*` JSON 表面**，与现有 HTML 路由并存：

- **API Key Bearer 认证**（兼容现有 session 双路径）
- **Resource Controller 约定**（list/get/create/patch/destroy）
- **RFC 7807 Problem Details 错误格式**
- **统一分页 + OpenAPI 文档**
- **审计日志**（写操作 + 错误调用）

V1 资源范围：work-orders / serial-units / defects / api-keys。其他资源（planner schedule、defect handle actions、operation-records）下个 spec 加。

### 1.3 非目标（明确不做）

- Webhook 订阅（D 层 Agent Gateway 时做）
- Rate limiting（项目级，deferred）
- Cursor-based 分页（先 page/size）
- ETag / conditional GET（先简化）
- Per-module scopes（user role 已管，避免双重配置）
- Planner schedule/unschedule JSON 端点
- Defect handle-rework/scrap/concession JSON 端点

---

## 2. 总体架构

**双面 MES** —— 同一 service 层，两条 HTTP 表面：

```
                       ┌──────────────────────┐
                       │   Service 层         │
                       │  (PlannerService,    │
                       │   ProductionService, │
                       │   DefectService...)  │
                       └──────────┬───────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
   ┌────────▼─────────┐  ┌────────▼─────────┐  ┌────────▼─────────┐
   │  HTML Router     │  │  /api/v1 Router  │  │  (future)        │
   │  (操作员 UI)     │  │  (Agent/外部)    │  │  /agents/v1 MCP  │
   │  cookie session  │  │  API Key Bearer  │  │                  │
   └──────────────────┘  └──────────────────┘  └──────────────────┘
```

**关键原则**：HTML 路由不动。新增 `/api/v1/*` 路由调用同一 service 层。两者长期共存。

---

## 3. API Key 模型

### 3.1 数据模型

```python
class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()                       # "ERP Sync" / "BI Dashboard"
    key_prefix: Mapped[str] = mapped_column(index=True)       # "lmk_live_a3f2" 前 12 字符
    key_hash: Mapped[str] = mapped_column()                   # Argon2 散列完整 key
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # ["read","write"] 或 ["read"]
    is_active: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    last_used_ip: Mapped[str | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
```

### 3.2 Key 格式

- 完整 key：`lmk_live_<32 chars>` 或 `lmk_test_<32 chars>`
- 前缀 `lmk_`（GitHub secret scanning 风格，方便扫描泄露）
- `live` / `test` 区分环境（test 可未来用于沙箱）
- 32 字符 `secrets.token_urlsafe()` 随机
- **完整 key 仅在创建时返回一次**，DB 只存 Argon2 hash

### 3.3 Scopes（简单 read/write）

- `read` —— GET 端点
- `write` —— POST/PATCH/DELETE 端点

**不做 per-module scope**：user 的 role（operator/supervisor/admin）已经管控模块级权限。API Key 只区分读/写，避免双重配置。

### 3.4 双路径认证

```python
def require_api_key(scopes: tuple[str, ...] = ()):
    """Validate Authorization: Bearer lmk_xxx, return User.

    Path 1: Bearer token (API consumer)
    Path 2: Fall back to session cookie (browser admin UI experimentation)

    Scopes: ('read',) / ('write',) / both. Key must have ALL required scopes.
    """
```

**设计要点**：
- API Key 持有者必须是某个 User（继承 role 权限，复用 `require_role`）
- 双路径让 TestClient 测试和 admin 浏览器实验都能工作

---

## 4. Resource Controller 约定

### 4.1 URL 与方法

```
GET    /api/v1/{resource}            list        ?page=1&size=20&filters...
GET    /api/v1/{resource}/{id}       get one
POST   /api/v1/{resource}            create
PATCH  /api/v1/{resource}/{id}       partial update
DELETE /api/v1/{resource}/{id}       delete (if supported)

Special:
GET    /api/v1/serial-units/by-sn/{sn}    lookup by business key
```

**避免**：
- `PUT`（用 PATCH 替代）
- 嵌套超过 2 层（`/work-orders/{id}/defects` 反模式；用 `/defects?work_order_id=X`）
- Verbs in path（`/schedule-work-order` 反模式）—— 动作用子资源（`POST /work-orders/{id}/schedule`）

### 4.2 响应格式

**裸数据**（FastAPI `response_model` idiom），不加包络：

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Total-Count: 42
X-Page: 1
X-Size: 20

[
  {"id": 1, "code": "WO-001", ...},
  ...
]
```

理由：FastAPI/Pydantic 原生支持、OpenAPI 描述简洁、Agent 解析直接。HTTP status 已表达成败，包络 `{code, data, message}` 是冗余。

### 4.3 分页

| 参数 | 默认 | 上限 | 说明 |
|---|---|---|---|
| `page` | 1 | — | 从 1 开始 |
| `size` | 20 | 100 | 每页数量 |
| `sort` | 资源特定 | — | 格式 `field` 或 `-field`（desc） |

响应头：`X-Total-Count`, `X-Page`, `X-Size`。

### 4.4 过滤

每资源特定，统一风格：
- `?status=active` — 单值
- `?status=active&status=pending` — 多值（OR）
- `?line_id=1` — ID 过滤
- `?created_since=2026-08-01` — 时间范围前缀（`created_before` / `created_since`）

---

## 5. 错误格式（RFC 7807 Problem Details）

```http
HTTP/1.1 409 Conflict
Content-Type: application/problem+json

{
  "type": "https://lightmes/errors/ConflictError",
  "title": "Conflict",
  "status": 409,
  "detail": "产线 1 时段 2026-08-12T08:00 ~ 2026-08-12T16:00 已被工单 WO-X 占用",
  "instance": "/api/v1/work-orders/123",
  "trace_id": "abc-123-def"
}
```

### 5.1 DomainError → HTTP 映射

| 异常 | Status | 说明 |
|---|---|---|
| `ValidationError` | 400 | 入参校验失败 |
| `NotFoundError` | 404 | 资源不存在 |
| `ConflictError` | 409 | 状态冲突（如时段重叠） |
| `BusinessRuleError` | 422 | 业务规则违反 |
| 未捕获 `Exception` | 500 | 服务端错误（脱敏 detail） |

### 5.2 trace_id

- FastAPI middleware 给每个请求生成 `request.state.trace_id = uuid4().hex[:8]`
- 异常处理器 + ApiCallLog 共用同一 ID
- Agent 报错时凭 trace_id 可在服务端日志/审计表定位完整链路

---

## 6. V1 端点清单

### 6.1 Work Orders（4 endpoints）

```
GET    /api/v1/work-orders?page=1&size=20&status=&line_id=&created_since=
GET    /api/v1/work-orders/{id}
POST   /api/v1/work-orders                          # 仅 created 状态，需要 admin/supervisor
PATCH  /api/v1/work-orders/{id}/priority            # 调整优先级（demo write）
```

**POST Payload**：
```json
{
  "code": "WO-2026-001",
  "product_id": 1,
  "routing_id": 2,
  "line_id": 1,
  "qty": 100,
  "sn_rule_id": 1,
  "priority": 5
}
```

复用现有 `ProductionService.create_work_order`。Returns `WorkOrderReadV1`（含 `priority`，无 `process_snapshot` 等内部字段）。

### 6.2 Serial Units（3 endpoints）

```
GET    /api/v1/serial-units?page=1&size=20&work_order_id=&status=&sn=
GET    /api/v1/serial-units/{id}
GET    /api/v1/serial-units/by-sn/{sn}              # 业务键查询
```

**Returns `SerialUnitReadV1`**：
```json
{
  "id": 1,
  "sn": "X0001",
  "work_order_id": 1,
  "product_id": 1,
  "status": "in_process",
  "current_operation_seq": 3,
  "is_counted": false,
  "carrier_code": "CARRIER-A",
  "created_at": "2026-08-12T..."
}
```

包含 `current_operation_seq` —— Agent 不需要查 operation_records 就能知道当前工序。

### 6.3 Defects（2 endpoints）

```
GET    /api/v1/defects?page=1&size=20&status=&severity=&work_order_id=
GET    /api/v1/defects/{id}
```

**Returns `DefectReadV1`**：
```json
{
  "id": 1,
  "defect_type_code": "FIRST_INSPECTION_FAIL",
  "defect_type_name": "首检不合格",
  "severity": "critical",
  "serial_unit_id": 5,
  "work_order_id": 1,
  "operation_id": 3,
  "handling_status": "pending",
  "discovered_at": "2026-08-12T...",
  "handled_at": null,
  "remark": null
}
```

写操作（返工/报废/让步）本 spec 不开放 —— 已有 admin/supervisor HTML 路由，下个 spec 设计 Agent 友好端点（需更仔细的卡控）。

### 6.4 API Keys 管理（admin only）

```
GET    /api/v1/api-keys                             # 列出（不含 key 全文）
POST   /api/v1/api-keys                             # 创建（返回 key 全文一次）
DELETE /api/v1/api-keys/{id}                        # 吊销
```

外加 HTML 管理页 `/system/api-keys`（admin 用户管理 key）。

---

## 7. 审计日志（ApiCallLog）

### 7.1 记录策略

| 条件 | 记录 |
|---|---|
| 写操作（POST/PATCH/DELETE） | **总是** |
| 失败（>=400） | **总是** |
| 成功的 GET | **不记录** |

避免表爆炸的同时保留关键追溯能力。

### 7.2 Schema

```python
class ApiCallLog(Base, TimestampMixin):
    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column()                # GET/POST/PATCH/DELETE
    path: Mapped[str] = mapped_column(index=True)        # /api/v1/work-orders
    status_code: Mapped[int] = mapped_column(index=True)
    duration_ms: Mapped[int] = mapped_column()
    trace_id: Mapped[str | None] = mapped_column(index=True)
    client_ip: Mapped[str | None] = mapped_column(default=None)
    error_detail: Mapped[str | None] = mapped_column(default=None)  # 失败时存 detail（截断 500 字）
```

实现：FastAPI middleware，只对 `/api/v1/*` 路径生效。

---

## 8. OpenAPI 元数据

每个端点声明 tag / summary / description：

```python
@router.get("/work-orders/{wo_id}", response_model=WorkOrderReadV1,
            tags=["Work Orders"], summary="Get a work order by ID",
            description="Retrieve a single work order with its current state.")
```

FastAPI 自动生成 `/api/v1/openapi.json` + `/docs` Swagger UI。Agent 可消费 OpenAPI 自动发现能力。

Tag 分组：
- `Work Orders`
- `Serial Units`
- `Defects`
- `API Keys`

---

## 9. 测试策略

| 测试 | 覆盖点 |
|---|---|
| `test_api_key_create_returns_full_key_once` | POST /api/v1/api-keys 返回 full_key |
| `test_api_key_hash_not_stored_plaintext` | DB 中只存 Argon2 |
| `test_api_key_auth_valid_bearer_token` | 正确 key 通过 require_api_key |
| `test_api_key_auth_invalid_format` | 不带 lmk_ 前缀 → 401 |
| `test_api_key_auth_revoked_key` | 吊销后立即失效 |
| `test_api_key_auth_expired_key` | expires_at 过去 → 401 |
| `test_api_key_fallback_to_session` | 无 Authorization header 但有 session → 通过（双路径） |
| `test_api_key_scopes_enforced` | read-only key 调 write endpoint → 403 |
| `test_work_orders_list_pagination` | page/size + X-Total-Count |
| `test_work_orders_list_filters` | status/line_id/created_since |
| `test_work_orders_create_validates_payload` | 缺字段 → 422 |
| `test_work_orders_create_requires_write_scope` | read key → 403 |
| `test_serial_units_by_sn` | GET /by-sn/{sn} 找到/找不到 |
| `test_defects_list_filters` | status/severity/work_order_id |
| `test_problem_details_error_format` | DomainError → application/problem+json |
| `test_trace_id_present_on_error` | 错误响应含 trace_id |
| `test_api_call_log_records_writes_and_errors` | 写/失败 记录；GET 不记录 |
| `test_openapi_json_generated` | GET /api/v1/openapi.json 200 |

---

## 10. 安全考量

1. **HTTPS only in prod**：API Key 明文传网络，必须 HTTPS。`TrustProxies` 已配置。
2. **Key 不存明文**：DB 只有 Argon2 hash，前缀 12 字符存明文（列表识别，无敏感性）。
3. **创建时返回一次**：API Key 编辑页提示"复制保存，关闭后无法再查看"。
4. **吊销即生效**：`is_active=False` 或 `revoked_at != None` 立即拒绝。
5. **过期校验**：`expires_at`（可选）；不设则永久。
6. **Argon2 慢但安全**：每次 `/api/v1/*` 请求跑一次 Argon2（~100ms）。对 Agent 调用频率（<10/min）完全够用。若未来需要更高吞吐，再加 SHA256 缓存层。
7. **不可见性**：API Key 列表页只显示前缀 + 名称 + last_used_at，不显示 hash。

---

## 11. 任务拆分（预估 8 task）

1. **Migration + Models** — ApiKey + ApiCallLog tables
2. **API Key 服务 + require_api_key 依赖 + Problem Details 错误处理器 + trace_id middleware**
3. **API Key 管理 UI** — `/system/api-keys` 页面（admin only）+ list/create/revoke
4. **API Key JSON 端点** — `/api/v1/api-keys`（list/create/delete）
5. **Work Orders API** — `/api/v1/work-orders`（list/get/create/patch priority）
6. **Serial Units API** — `/api/v1/serial-units`（list/get/by-sn）
7. **Defects API + ApiCallLog middleware + OpenAPI tags** — `/api/v1/defects`（list/get）+ 审计 middleware
8. **回归 + memory 更新**

---

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Argon2 每次 ~100ms 拖慢 API | 对 Agent 调用频率（<10/min）完全够；高吞吐再加缓存层 |
| 双路径（Bearer + session）使 auth 复杂 | 集中在 `require_api_key` 单一依赖，HTML 路由不受影响 |
| API Key 泄露 | 前缀 `lmk_` 可扫描（GitHub secret scanning 风格）；吊销即生效 |
| ApiCallLog 表膨胀 | 只记写/失败，不记 GET 成功；未来加 90 天清理 cron |
| Resource Controller 约定与现有 HTML 路由风格不一致 | 接受 —— 两个表面服务不同消费者 |
| 两个表面（HTML + JSON）长期维护成本 | service 层共享，router 是薄层；新增能力只写一次 service |
