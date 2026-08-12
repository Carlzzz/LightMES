"""FastMCP server instance + mount helper.

`mount_mcp(app)` 挂载 FastMCP 到主 FastAPI app 的 `/mcp` 路径，并强制：
- 所有请求必须通过 `verify_bearer` Bearer token 认证（401 → `{"detail": ...}`）。
- FastMCP session manager lifespan 与主 app lifespan 合并（否则 session 初始化失败）。
- JSON 响应模式（`json_response=True`），客户端须 `Accept: application/json`。

实现说明：
- FastAPI 0.141 的 `app.mount()` 不支持 `dependencies=` 参数，因此认证不能直接
  通过 FastAPI dependency 注入挂载子 app。我们用 Starlette `BaseHTTPMiddleware`
  包裹 mcp_app，在中间件里手动调用 `verify_bearer` 的核心逻辑（含手动解析
  `Authorization` 头与 `get_db`，并尊重 `app.dependency_overrides` 以支持测试）。
- FastMCP 2.14.7 没有 `fastmcp.utilities.lifespan.combine_lifespans` helper；
  这里手写等价的 `@asynccontextmanager` 合并两个 lifespan。
"""
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastmcp import FastMCP

mcp = FastMCP(
    name="LightMES",
    instructions=(
        "LightMES Manufacturing Execution System for notebook shell assembly. "
        "Use these tools to query production status, schedule work orders, "
        "and report defects. Most write operations require admin/supervisor role."
    ),
)

# mount_mcp(app) 时填充；中间件需要它来解析 dependency_overrides（测试用）
_parent_app: FastAPI | None = None

# FastMCP 的 StreamableHTTPSessionManager.run() 只能调用一次；
# 测试中 `with TestClient(app)` 会反复 enter/exit，我们在 lifespan exit
# 时重置 session manager 的 _has_started 标志以允许重复 run()。
_mcp_app_ref = None


def _resolve_db_session() -> Iterator[Session]:
    """手动解析 db session，尊重 _parent_app.dependency_overrides[get_db]。

    生产路径：调用 `lightmes.database.get_db()` 生成器。
    测试路径：`app.dependency_overrides[get_db]` 会被测试设置为
    `lambda: db_session`，这里取到 override 后直接 yield。
    """
    from lightmes.database import get_db
    if _parent_app is not None:
        override = _parent_app.dependency_overrides.get(get_db)
        if override is not None:
            yield override()
            return
    # 生产路径：用真实 get_db 生成器
    yield from get_db()


def _build_auth_middleware(verify_bearer):
    """构造一个 Starlette 中间件：在请求进入 FastMCP 之前执行 verify_bearer。

    FastAPI 0.141 的 `app.mount()` 不接受 `dependencies=` 参数，因此无法直接
    用 FastAPI dependency 给挂载的子 app 加认证。我们退而使用 Starlette
    `BaseHTTPMiddleware`：在 dispatch 中显式调用 `verify_bearer` 的核心逻辑，
    捕获 `HTTPException` 并返回与 FastAPI 默认错误格式一致的 JSON 响应
    (`{"detail": "..."}`)。
    """

    class _BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            authorization = request.headers.get("authorization")
            db_iter = _resolve_db_session()
            try:
                db = next(db_iter)
            except StopIteration:
                # 不应该发生；保险起见返回 500
                return JSONResponse(
                    {"detail": "DB session 解析失败"}, status_code=500
                )
            try:
                try:
                    # verify_bearer 是 async 函数；直接调用其纯逻辑部分
                    # 这里复用其签名 (request, authorization, db)
                    await verify_bearer(request, authorization, db)
                except HTTPException as exc:
                    return JSONResponse(
                        {"detail": exc.detail}, status_code=exc.status_code
                    )
                return await call_next(request)
            finally:
                # 关闭生成器（触发 finally / rollback / close）
                try:
                    next(db_iter, None)
                except Exception:
                    pass

    return _BearerAuthMiddleware


def _wrap_lifespan(parent_lifespan, mcp_lifespan):
    """合并 parent app lifespan 与 FastMCP 子 app lifespan。

    手写等价的 `combine_lifespans`：先进入 mcp lifespan（初始化
    StreamableHTTPSessionManager task group），再进入 parent lifespan。

    FastMCP 2.14.7 的 StreamableHTTPSessionManager.run() 只能被调用一次
    （再次 enter 会 raise RuntimeError）。生产环境只 enter 一次没问题；
    但测试用 `with TestClient(app)` 会反复 enter/exit lifespan，每次都
    在新 event loop 上。我们通过在 lifespan exit 时把 mcp 的
    `_has_started` 标志位重置回 False 来允许下一次 enter 重新初始化
    task group（FastMCP 内部私有 API，但 2.14.7 行为稳定）。
    """

    @asynccontextmanager
    async def _combined(app):
        try:
            async with mcp_lifespan(app):
                if parent_lifespan is None:
                    yield
                else:
                    async with parent_lifespan(app):
                        yield
        finally:
            # 允许下一次 lifespan enter 重新 run() session manager
            _reset_mcp_session_manager()

    return _combined


def _reset_mcp_session_manager() -> None:
    """重置 FastMCP session manager 的 `_has_started` 标志，允许重复 run()。

    FastMCP 2.14.7 的 StreamableHTTPSessionManager 在 run() exit 后会把
    `_task_group` 置 None 但保留 `_has_started = True`，导致下一次 run() 抛
    RuntimeError。这里在 lifespan exit 时把 `_has_started` 也置回 False，
    让多次 TestClient enter/exit 在测试场景下能正常工作。
    生产环境下 lifespan 只 exit 一次（进程关闭），此重置无害。
    """
    from fastmcp.server.http import StreamableHTTPASGIApp

    # mcp_app 是 mount_mcp 创建的；通过 app.state 找回 session_manager
    if _mcp_app_ref is None:
        return
    # mcp_app.routes[0].app 是 StreamableHTTPASGIApp
    try:
        for route in _mcp_app_ref.router.routes:
            endpoint = getattr(route, "app", None)
            if isinstance(endpoint, StreamableHTTPASGIApp):
                session_manager = endpoint.session_manager
                session_manager._has_started = False
    except Exception:
        # best-effort，任何失败都不影响 lifespan 正常退出
        pass


def _get_parent_lifespan(app: FastAPI):
    """FastAPI 0.141 没有 app.lifespan 属性，lifespan 存在 router 上。"""
    return getattr(app.router, "lifespan_context", None)


def _set_parent_lifespan(app: FastAPI, new_lifespan) -> None:
    app.router.lifespan_context = new_lifespan


def mount_mcp(app: FastAPI) -> None:
    """挂载 MCP server 到 FastAPI app 的 `/mcp`，强制 Bearer auth。"""
    global _parent_app, _mcp_app_ref
    _parent_app = app

    # 延迟导入避免循环依赖（main.py → agent_gateway.register → mount_mcp → auth）
    from lightmes.modules.agent_gateway.auth import verify_bearer

    # 触发 tool 模块导入 —— `@mcp.tool()` 装饰器在 import 时执行注册。
    # 必须在 `mcp.http_app()` 之前完成，否则 tools/list 为空。
    from lightmes.modules.agent_gateway.tools import (  # noqa: F401
        api_keys, defects, serial_units, work_orders,
    )

    # FastMCP http_app 返回 StarletteWithLifespan（ASGI app）。
    # path='/' 让 FastMCP 内部把单条 MCP 路由注册到根 `/`，然后我们用
    # `app.mount("/mcp", ...)` 把它挂到 `/mcp` 上 —— 这样 mcp_app 只接管
    # `/mcp/...` 请求，其它路径（/, /api/v1, /health 等）仍走主 app 路由。
    # json_response=True 以获得纯 JSON 响应（默认 SSE 需要 `data:` 行解析）。
    mcp_app = mcp.http_app(path="/", json_response=True)
    _mcp_app_ref = mcp_app

    # 1) 给 mcp_app 加 Bearer auth 中间件（所有 /mcp/* 请求都过这里）
    mcp_app.add_middleware(_build_auth_middleware(verify_bearer))

    # 2) 合并 lifespan：FastMCP session manager 需要在 app lifespan 中初始化
    parent_lifespan = _get_parent_lifespan(app)
    mcp_lifespan = mcp_app.lifespan
    _set_parent_lifespan(app, _wrap_lifespan(parent_lifespan, mcp_lifespan))

    # 3) 挂载到 `/mcp`：FastMCP 内部路由已在 `/`，叠加 mount 前缀即 `/mcp/`。
    app.mount("/mcp", mcp_app)
