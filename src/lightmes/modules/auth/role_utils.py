"""共享角色工具。保持无侧效 import（不引入 templates / fastapi 路由层），避免循环。"""
from __future__ import annotations


def user_role_name(user) -> str | None:
    """读取用户角色名：优先 role_obj.name，回退 legacy role 字段（兼容旧用户）。"""
    if user is None:
        return None
    if getattr(user, "role_obj", None) is not None:
        return user.role_obj.name
    return getattr(user, "role", None)


def is_privileged(user) -> bool:
    """supervisor / admin 看全部资源（issue 等）。"""
    return user_role_name(user) in ("supervisor", "admin")
