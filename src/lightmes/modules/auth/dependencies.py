from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from lightmes.database import get_db
from lightmes.modules.auth.models import User
from lightmes.modules.auth.service import AuthService

# 角色权限层级（数字越大权限越高）- 向后兼容
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


def require_permission(resource: str, action: str):
    """FastAPI dependency factory: require the user to have specific permission.

    Usage::

        @router.post("/api/something", dependencies=[Depends(require_permission("production:workOrder", "write"))])
        def handler(...): ...

    Or to get the user object::

        @router.post("/api/something")
        def handler(user: User = Depends(require_permission("production:workOrder", "write"))): ...

    Raises 403 if the user doesn't have the required permission.
    """
    def _check(user: User = Depends(require_login), db: Session = Depends(get_db)) -> User:
        auth_service = AuthService(db)
        if not auth_service.check_permission(user.id, resource, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {resource}:{action}",
            )
        return user
    return _check


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

    def _check(user: User = Depends(require_login), db: Session = Depends(get_db)) -> User:
        # 先检查新的role_obj.name（SQLAlchemy 2.0 select 语法）
        full_user = db.execute(
            select(User).where(User.id == user.id).options(joinedload(User.role_obj))
        ).scalar_one_or_none()

        if full_user and full_user.role_obj:
            user_level = ROLE_LEVEL.get(full_user.role_obj.name, 0)
            if user_level >= min(allowed_levels):
                return user

        # 向后兼容：检查旧的role字段（legacy DB column, 可能为 None）
        legacy_role = getattr(user, "role", None)
        if legacy_role:
            user_level = ROLE_LEVEL.get(legacy_role, 0)
            if user_level >= min(allowed_levels):
                return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足：需要 {', '.join(allowed_roles)} 角色",
        )

    return _check


def require_role_name(*allowed_role_names: str):
    """FastAPI dependency factory: require the user to have one of the role names.

    For backward compatibility with the old role system.
    """
    def _check(user: User = Depends(require_login), db: Session = Depends(get_db)) -> User:
        full_user = db.execute(
            select(User).where(User.id == user.id).options(joinedload(User.role_obj))
        ).scalar_one_or_none()
        if full_user and full_user.role_obj and full_user.role_obj.name in allowed_role_names:
            return user
        # 向后兼容：如果用户有旧的role字段也检查
        legacy_role = getattr(user, "role", None)
        if legacy_role and legacy_role in allowed_role_names:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足：需要 {', '.join(allowed_role_names)} 角色",
        )
    return _check


# 向后兼容的辅助函数
def can_edit_masterdata(user: User | None, db: Session) -> bool:
    """主数据修改权限：检查是否有 masterdata:*:write 或 admin"""
    if user is None:
        return False
    auth_service = AuthService(db)
    return auth_service.check_permission(user.id, "masterdata:products", "write") or \
           auth_service.check_permission(user.id, "masterdata:routings", "write") or \
           auth_service.check_permission(user.id, "masterdata:*", "*")


def can_rework(user: User | None, db: Session) -> bool:
    """返工/判废权限：检查 trace:rework"""
    if user is None:
        return False
    auth_service = AuthService(db)
    return auth_service.check_permission(user.id, "trace", "rework")


def can_operate(user: User | None, db: Session) -> bool:
    """过站操作权限：检查 production:station:use"""
    if user is None:
        return False
    auth_service = AuthService(db)
    return auth_service.check_permission(user.id, "production:station", "use")


def can_view(user: User | None) -> bool:
    """查看权限：所有登录用户"""
    return user is not None
