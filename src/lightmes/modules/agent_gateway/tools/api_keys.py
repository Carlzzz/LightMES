"""API Key MCP tools (admin only — write scope 隐含 admin/supervisor 角色)。

3 tools:
- list_api_keys (read) — 列出当前 user 的 keys（不含 hash/full_key）
- create_api_key (write) — 创建新 key，full_key 仅返回一次
- revoke_api_key (write) — 吊销自己的 key

工具仅操作「当前认证用户」的 keys，不接受任意 user_id 参数，避免越权。
"""
from datetime import datetime

from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    ApiKeyCreatedResponse, ApiKeyRead,
)
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_api_keys() -> list[ApiKeyRead]:
    """列出当前认证用户的 API Keys（不含 key_hash 或 full_key）。"""
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.api_v1.api_key_service import ApiKeyService

    request = get_http_request()
    db = request.state.db_session
    user = request.state.user
    keys = ApiKeyService(db).list_for_user(user.id)
    return [ApiKeyRead.model_validate(k) for k in keys]


@mcp.tool()
@require_scope("write")
def create_api_key(
    name: str,
    scopes: list[str] | None = None,
    expires_at: str | None = None,
) -> ApiKeyCreatedResponse:
    """创建新 API Key（write scope，需 admin/supervisor 角色）。

    Args:
        name: Key 名称（1-100 字符）。
        scopes: 权限 scope 列表，默认 ["read"]。可选值: read, write。
        expires_at: 可选，ISO 8601 字符串（如 "2026-12-31T23:59:59"）。

    Returns:
        含 `full_key` 的响应 —— `full_key` 仅此一次返回，须客户端立即保存。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.api_v1.api_key_service import ApiKeyService

    request = get_http_request()
    db = request.state.db_session
    user = request.state.user
    exp_dt = datetime.fromisoformat(expires_at) if expires_at else None
    full_key, record = ApiKeyService(db).create(
        name=name, user_id=user.id,
        scopes=scopes or ["read"], expires_at=exp_dt)
    db.flush()
    return ApiKeyCreatedResponse(
        id=record.id, name=record.name, key_prefix=record.key_prefix,
        scopes=record.scopes, full_key=full_key, created_at=record.created_at,
    )


@mcp.tool()
@require_scope("write")
def revoke_api_key(api_key_id: int) -> dict:
    """吊销 API Key（仅可吊销自己的；write scope）。

    Args:
        api_key_id: 待吊销的 API Key id。

    Raises:
        NotFoundError: 目标 key 不存在或不属于当前用户。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    from lightmes.modules.auth.models import ApiKey
    from lightmes.shared.errors import NotFoundError

    request = get_http_request()
    db = request.state.db_session
    user = request.state.user
    target = db.get(ApiKey, api_key_id)
    if target is None or target.user_id != user.id:
        raise NotFoundError(f"API Key 不存在: {api_key_id}")
    ApiKeyService(db).revoke(api_key_id, revoked_by_user_id=user.id)
    db.flush()
    return {"ok": True}
