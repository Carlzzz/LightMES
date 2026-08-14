from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lightmes.config import get_settings
from lightmes.database import get_db
from lightmes.modules import (
    agent_gateway,
    api_v1,
    auth,
    connectivity,
    integration,
    inventory,
    issue,
    masterdata,
    production,
    trace,
    quality,
)
from lightmes.database import engine
from lightmes.shared.base import Base
from lightmes.modules.auth.dependencies import current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.modules.api_v1.errors import register_problem_details_handler
from lightmes.modules.api_v1.middleware import ApiCallLogMiddleware, TraceIdMiddleware
from lightmes.modules.issue.linkify import issue_linkify
from lightmes.shared.audit import AuditContextMiddleware, register_audit_listeners
from lightmes.shared.audit_router import audit_router
from lightmes.shared.extensions import extension_registry
from lightmes.shared.realtime import realtime_shape_registry
from lightmes.shared.web_security import (
    CsrfMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)

settings = get_settings()
if settings.environment == "production" and settings.secret_key == "change-me-in-prod":
    raise RuntimeError("生产环境必须设置非默认 SECRET_KEY")

try:
    _app_version = version("lightmes")
except PackageNotFoundError:
    _app_version = "0.1.0"

@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment != "production":
        Base.metadata.create_all(bind=engine)

    from lightmes.database import SessionLocal
    from lightmes.modules.auth.service import AuthService
    from lightmes.modules.production.defect_service import DefectService

    db = SessionLocal()
    try:
        AuthService(db).initialize_default_roles()
        admin_password = settings.admin_initial_password or None
        if admin_password:
            AuthService(db).ensure_admin_user(admin_password)
        DefectService(db).ensure_system_defect_types()
        db.commit()
    finally:
        db.close()

    yield

app = FastAPI(
    title=settings.app_name,
    version=_app_version,
    lifespan=lifespan,
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
app.add_middleware(
    CsrfMiddleware,
    exempt_prefixes=("/api/", "/mcp"),
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.environment == "production",
    max_age=settings.session_max_age_seconds,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_hosts,
)
app.add_middleware(
    RateLimitMiddleware,
    login_limit=settings.login_rate_limit,
    api_limit=settings.api_rate_limit,
    window_seconds=settings.rate_limit_window_seconds,
)
app.add_middleware(
    SecurityHeadersMiddleware,
    enable_hsts=settings.environment == "production",
)
app.add_middleware(AuditContextMiddleware)

from lightmes.modules.auth import models as _auth_models
from lightmes.modules.masterdata import models as _masterdata_models
from lightmes.modules.production import models as _production_models
from lightmes.modules.issue import models as _issue_models

register_audit_listeners(
    (
        _auth_models.User,
        _auth_models.Role,
        _auth_models.ApiKey,
        _masterdata_models.Product,
        _masterdata_models.Routing,
        _masterdata_models.Bom,
        _masterdata_models.BomItem,
        _masterdata_models.Line,
        _masterdata_models.WorkStation,
        _masterdata_models.Operation,
        _production_models.WorkOrder,
        _production_models.SerialUnit,
        _production_models.OperationRecord,
        _production_models.OperationParam,
        _production_models.DefectType,
        _production_models.DefectRecord,
        _production_models.Batch,
        _production_models.MaterialLot,
        _production_models.StockMovement,
        _production_models.BatchMaterialConsumption,
        _issue_models.Issue,
        _issue_models.IssueAction,
    )
)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
auth.register(app)
app.include_router(audit_router)
issue.register(app)
masterdata.register(app)
production.register(app)
trace.register(app)
integration.register(app)
inventory.register(app)
quality.register(app)
connectivity.register(app)
api_v1.register(app)
agent_gateway.register(app)

# Middleware（顺序：后加的在外层；先加 ApiCallLog，再加 TraceId → TraceId 在最外层，先注入 trace_id）
app.add_middleware(ApiCallLogMiddleware)
app.add_middleware(TraceIdMiddleware)

# 升级 DomainError handler 为 RFC 7807 Problem Details JSON
register_problem_details_handler(app)


_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
_templates.env.filters["issue_linkify"] = issue_linkify


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # 首页要求登录：未登录跳 /login，避免"看到首页但点功能被踢回登录"
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})
    from lightmes.modules.issue.repository import IssueRepository

    issue_repo = IssueRepository(db)
    open_count = issue_repo.count_open()
    blocking_count = issue_repo.count_blocking()
    return _templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "issue_open_count": open_count,
            "issue_blocking_count": blocking_count,
            "widgets": extension_registry.all_widgets(),
        },
    )


@app.get("/api/realtime/shapes")
def realtime_shapes() -> dict:
    registry = realtime_shape_registry
    return {
        name: {
            "table": shape.table,
            "columns": list(shape.columns),
            "where": shape.where,
        }
        for name in registry.names()
        if (shape := registry.find(name)) is not None
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready", "app": settings.app_name}
