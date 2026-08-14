"""Auth dependencies for MCP gateway.

Provides:
- `verify_bearer`: callable that validates `Authorization: Bearer lmk_xxx` via
  the C-layer `ApiKeyService` and injects `user` / `api_key` / `db_session`
  onto `request.state` so that downstream MCP tools (Task 3+) can read them.
  Designed to be called from a Starlette middleware in `server.py` (FastAPI
  `app.mount()` does not support `dependencies=`, so we cannot rely on DI).
  The signature `(request, authorization, db)` mirrors the FastAPI dependency
  contract for future direct use as `Depends(verify_bearer)` if FastAPI adds
  mount-level dependencies.
- `_has_write_role`: helper that checks admin/supervisor role, reusing the
  same pattern as `lightmes.modules.api_v1.dependencies`.
- `require_scope(scope)`: decorator for MCP tools (Task 3). Reads the injected
  `request.state.{api_key,user,db_session}` via FastMCP's `get_http_request()`
  and enforces (a) the API Key grants the required scope and (b) for `write`
  scope, the user has admin/supervisor role. Must be applied as the INNER
  decorator under `@mcp.tool()` so FastMCP sees the tool function with a
  proper signature.
"""
import functools

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.auth.models import User
from lightmes.shared.audit import set_audit_user

# 写操作 scope 所需的最小角色集合（与 api_v1/dependencies.py 保持一致）
_WRITE_REQUIRED_ROLES = {"admin", "supervisor"}


def _has_write_role(user: User, db: Session) -> bool:
    """检查用户是否拥有 admin/supervisor 角色。

    优先使用新 ORM 关系 `role_obj`，并保留对 legacy `role` 字段的兜底。
    """
    full_user = db.execute(
        select(User).where(User.id == user.id).options(joinedload(User.role_obj))
    ).scalar_one_or_none()
    if full_user and full_user.role_obj is not None:
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
    """验证 `Authorization: Bearer lmk_xxx`，注入 user/api_key/db_session 到 request.state。

    任何认证失败均抛 `HTTPException(401)`。本函数既可作为 FastAPI dependency
    使用（signature 含 `Header` / `Depends`），也可被 `server.py` 的中间件
    直接 `await verify_bearer(request, authorization_str, db_session)` 调用
    —— 中间件传字符串 / Session 实参绕过 FastAPI 默认值的解析。
    """
    if not authorization or not authorization.startswith("Bearer lmk_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 Bearer lmk_xxx token",
        )
    full_key = authorization[len("Bearer "):]
    # ApiKeyService.validate 自身会 raise HTTPException(401) 处理无效 / 过期 / 吊销场景
    user, api_key = ApiKeyService(db).validate(full_key)
    # 更新 last_used_at / last_used_ip（与 C-layer require_api_key 保持一致）
    from datetime import datetime
    api_key.last_used_at = datetime.now()
    api_key.last_used_ip = request.client.host if request.client else None
    db.flush()
    # 注入到 request.state 供 MCP tools 访问（Task 3+）
    request.state.user = user
    request.state.api_key = api_key
    request.state.db_session = db
    set_audit_user(user.id)
    return user


def require_scope(scope: str):
    """装饰器：MCP tool 的 scope/role 双重检查。

    使用方式（必须 `@mcp.tool()` 在外，`@require_scope(...)` 在内）::

        @mcp.tool()
        @require_scope("read")
        def list_work_orders(...): ...

        @mcp.tool()
        @require_scope("write")
        def create_work_order(...): ...

    实现：
    - 通过 `get_http_request()`（FastMCP 2.x 提供，基于 contextvar）拿到当前
      starlette Request。`server.py` 的 Bearer auth 中间件已经在
      `request.state` 上注入了 `api_key` / `user` / `db_session`。
    - scope 不匹配 → `PermissionError`（FastMCP 转为 MCP error response）。
    - scope == "write" → 额外检查 user 角色必须是 admin/supervisor
      （与 `lightmes.modules.api_v1.dependencies.require_api_key` 保持一致）。
    - 不在 HTTP 上下文（如本地脚本直接调用 tool）→ 跳过检查，便于开发期调试。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from fastmcp.server.dependencies import get_http_request

            request = get_http_request()
            if request is None:
                # 不在 HTTP 上下文（本地直接调用），跳过 scope 检查
                return func(*args, **kwargs)
            api_key = getattr(request.state, "api_key", None)
            user = getattr(request.state, "user", None)
            db = getattr(request.state, "db_session", None)
            if api_key is None or user is None:
                raise PermissionError("未通过认证（无 API Key / User）")
            granted = set(getattr(api_key, "scopes", None) or [])
            if scope not in granted:
                raise PermissionError(f"API Key 缺少 scope: {scope}")
            if scope == "write" and not _has_write_role(user, db):
                raise PermissionError("写操作需要 admin/supervisor 角色")
            return func(*args, **kwargs)

        return wrapper

    return decorator
