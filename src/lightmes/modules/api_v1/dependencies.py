from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.auth.dependencies import current_user_or_none
from lightmes.modules.auth.models import User


def require_api_key(*scopes: str):
    """FastAPI dependency factory.

    Validates `Authorization: Bearer lmk_xxx` OR falls back to session cookie.
    Returns the User object. Raises 401 if neither path succeeds, 403 if scopes
    insufficient.

    Usage::

        @router.get("/work-orders", dependencies=[Depends(require_api_key("read"))])
        def handler(...): ...

        @router.post("/work-orders")
        def handler(user: User = Depends(require_api_key("read", "write"))): ...
    """
    required = set(scopes) if scopes else set()

    async def _check(
        request: Request,
        authorization: str | None = Header(default=None),
        db: Session = Depends(get_db),
    ) -> User:
        # Path 1: Bearer token
        if authorization and authorization.startswith("Bearer lmk_"):
            full_key = authorization[len("Bearer "):]
            user, api_key = ApiKeyService(db).validate(full_key)
            # Update last_used_at / last_used_ip (best effort)
            from datetime import datetime
            api_key.last_used_at = datetime.now()
            api_key.last_used_ip = request.client.host if request.client else None
            db.flush()
            # Stash for ApiCallLog middleware
            request.state.api_key_id = api_key.id
            request.state.api_key_user_id = user.id
            # Scope check
            granted = set(api_key.scopes or [])
            missing = required - granted
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"API Key 缺少 scope: {', '.join(sorted(missing))}",
                )
            return user
        # Path 2: Session cookie fallback (for browser admin UI)
        # 防御：若 SessionMiddleware 未装（如独立 FastAPI 实例测试），跳过 session 路径
        user = None
        if "session" in request.scope:
            user = current_user_or_none(request, db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要 Bearer token 或登录会话",
            )
        request.state.api_key_user_id = user.id
        # Session path bypasses scope check (user's role gates it)
        return user

    return _check
