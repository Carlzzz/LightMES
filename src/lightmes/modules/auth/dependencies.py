from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.models import User

# 角色权限层级（数字越大权限越高）
ROLE_LEVEL: dict[str, int] = {
    "viewer": 10,
    "operator": 20,
    "supervisor": 30,
    "admin": 40,
}

ALL_ROLES = list(ROLE_LEVEL.keys())


def current_user_or_none(request: Request, db: Session) -> User | None:
    """Return the current User for a valid session, or None if not authenticated.

    Returns None when there is no session, the session user_id is not an int,
    the user no longer exists, or the user has been deactivated. Never raises.
    """
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: require an authenticated session, return the current User.

    Raises 401 if no valid session. Use on JSON API write endpoints.
    """
    user = current_user_or_none(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    return user


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: require the user to have one of *allowed_roles*.

    Usage::

        @router.post("/api/something", dependencies=[Depends(require_role("admin"))])
        def handler(...): ...

    Or to get the user object::

        @router.post("/api/something")
        def handler(user: User = Depends(require_role("admin", "supervisor"))): ...

    Raises 403 if the user's role is not in the allowed set.
    """
    allowed_levels = {ROLE_LEVEL.get(r, 0) for r in allowed_roles}

    def _check(user: User = Depends(require_login)) -> User:
        user_level = ROLE_LEVEL.get(user.role, 0)
        if user_level < min(allowed_levels):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {', '.join(allowed_roles)} 角色",
            )
        return user

    return _check


def can_edit_masterdata(user: User | None) -> bool:
    """主数据修改权限：admin only"""
    return user is not None and ROLE_LEVEL.get(user.role, 0) >= ROLE_LEVEL["admin"]


def can_rework(user: User | None) -> bool:
    """返工/判废权限：supervisor 及以上"""
    return user is not None and ROLE_LEVEL.get(user.role, 0) >= ROLE_LEVEL["supervisor"]


def can_operate(user: User | None) -> bool:
    """过站操作权限：operator 及以上"""
    return user is not None and ROLE_LEVEL.get(user.role, 0) >= ROLE_LEVEL["operator"]


def can_view(user: User | None) -> bool:
    """查看权限：所有登录用户"""
    return user is not None and ROLE_LEVEL.get(user.role, 0) >= ROLE_LEVEL["viewer"]
