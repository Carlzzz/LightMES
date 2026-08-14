from typing import Any
from sqlalchemy.orm import Session
from lightmes.modules.auth.models import User, Role, Permission, RolePermission
from lightmes.modules.auth.repository import (
    UserRepository, RoleRepository, PermissionRepository, RolePermissionRepository
)
from lightmes.modules.auth.schemas import (
    UserCreate, UserUpdate, RoleCreate, RoleUpdate, PermissionCreate
)
from lightmes.shared.security import hash_password, verify_password


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.user_repo = UserRepository(db)
        self.role_repo = RoleRepository(db)
        self.perm_repo = PermissionRepository(db)
        self.role_perm_repo = RolePermissionRepository(db)

    def create_user(self, data: UserCreate) -> User:
        if self.user_repo.get_by_username(data.username) is not None:
            raise ValueError(f"用户名已存在: {data.username}")
        user = User(
            username=data.username,
            password_hash=hash_password(data.password),
            display_name=data.display_name,
            role_id=data.role_id,
        )
        return self.user_repo.add(user)

    def update_user(self, user_id: int, data: UserUpdate) -> User:
        user = self.user_repo.get(user_id)
        if user is None:
            raise ValueError(f"用户不存在: {user_id}")
        if data.display_name is not None:
            user.display_name = data.display_name
        if data.role_id is not None:
            user.role_id = data.role_id
        if data.is_active is not None:
            user.is_active = data.is_active
        self.db.flush()
        return user

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.user_repo.get_by_username(username)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def get_user_permissions(self, user_id: int) -> list[str]:
        """获取用户的所有权限列表"""
        user = self.user_repo.get_with_role(user_id)
        if user is None:
            return []

        # 管理员拥有所有权限
        if user.role_obj and user.role_obj.name == "admin":
            return ["*"]

        permissions = []

        # 从角色JSON权限获取
        if user.role_obj and user.role_obj.permissions:
            # 简单格式: {"resources": {"production": ["read", "write"], ...}}
            if "resources" in user.role_obj.permissions:
                for resource, actions in user.role_obj.permissions["resources"].items():
                    for action in actions:
                        permissions.append(f"{resource}:{action}")

        # 从角色-权限关联表获取
        if user.role_obj:
            role_perms = self.role_perm_repo.list_by_role(user.role_obj.id)
            for rp in role_perms:
                perm = self.perm_repo.get(rp.permission_id)
                if perm:
                    permissions.append(f"{perm.resource}:{perm.action}")

        return permissions

    def check_permission(self, user_id: int, resource: str, action: str) -> bool:
        """检查用户是否拥有指定权限"""
        perms = self.get_user_permissions(user_id)
        if "*" in perms:
            return True
        if f"{resource}:*" in perms:
            return True
        if f"{resource}:{action}" in perms:
            return True
        return False

    def create_role(self, data: RoleCreate) -> Role:
        if self.role_repo.get_by_name(data.name) is not None:
            raise ValueError(f"角色名已存在: {data.name}")
        role = Role(
            name=data.name,
            display_name=data.display_name,
            description=data.description,
        )
        return self.role_repo.add(role)

    def update_role(self, role_id: int, data: RoleUpdate) -> Role:
        role = self.role_repo.get(role_id)
        if role is None:
            raise ValueError(f"角色不存在: {role_id}")
        if role.is_system:
            raise ValueError("系统角色不可修改")
        if data.display_name is not None:
            role.display_name = data.display_name
        if data.description is not None:
            role.description = data.description
        if data.is_active is not None:
            role.is_active = data.is_active
        if data.permissions is not None:
            role.permissions = data.permissions
        self.db.flush()
        return role

    def create_permission(self, data: PermissionCreate) -> Permission:
        if self.perm_repo.get_by_name(data.name) is not None:
            raise ValueError(f"权限名已存在: {data.name}")
        perm = Permission(
            name=data.name,
            display_name=data.display_name,
            resource=data.resource,
            action=data.action,
            description=data.description,
        )
        return self.perm_repo.add(perm)

    def assign_permission_to_role(self, role_id: int, permission_id: int, rules: dict | None = None) -> RolePermission:
        role = self.role_repo.get(role_id)
        if role is None:
            raise ValueError(f"角色不存在: {role_id}")
        if role.is_system:
            raise ValueError("系统角色不可修改")
        perm = self.perm_repo.get(permission_id)
        if perm is None:
            raise ValueError(f"权限不存在: {permission_id}")

        existing = self.role_perm_repo.get_by_role_and_permission(role_id, permission_id)
        if existing is not None:
            if rules is not None:
                existing.rules = rules
                self.db.flush()
            return existing

        rp = RolePermission(
            role_id=role_id,
            permission_id=permission_id,
            rules=rules,
        )
        return self.role_perm_repo.add(rp)

    def remove_permission_from_role(self, role_id: int, permission_id: int) -> None:
        rp = self.role_perm_repo.get_by_role_and_permission(role_id, permission_id)
        if rp:
            self.role_perm_repo.delete(rp.id)

    def initialize_default_roles(self) -> None:
        """初始化系统默认角色和权限"""
        # 创建默认权限
        default_perms = [
            # 主数据
            {"name": "masterdata:products:read", "display_name": "查看产品", "resource": "masterdata:products", "action": "read"},
            {"name": "masterdata:products:write", "display_name": "编辑产品", "resource": "masterdata:products", "action": "write"},
            {"name": "masterdata:routings:read", "display_name": "查看工艺", "resource": "masterdata:routings", "action": "read"},
            {"name": "masterdata:routings:write", "display_name": "编辑工艺", "resource": "masterdata:routings", "action": "write"},
            {"name": "masterdata:boms:read", "display_name": "查看BOM", "resource": "masterdata:boms", "action": "read"},
            {"name": "masterdata:boms:write", "display_name": "编辑BOM", "resource": "masterdata:boms", "action": "write"},
            # 生产
            {"name": "production:station:use", "display_name": "工位作业", "resource": "production:station", "action": "use"},
            {"name": "production:workOrder:read", "display_name": "查看工单", "resource": "production:workOrder", "action": "read"},
            {"name": "production:workOrder:write", "display_name": "编辑工单", "resource": "production:workOrder", "action": "write"},
            {"name": "production:wip:read", "display_name": "查看WIP", "resource": "production:wip", "action": "read"},
            # 追溯
            {"name": "trace:query", "display_name": "追溯查询", "resource": "trace", "action": "query"},
            {"name": "trace:rework", "display_name": "返工操作", "resource": "trace", "action": "rework"},
            # 质量
            {"name": "quality:firstInspection:read", "display_name": "查看首检", "resource": "quality:firstInspection", "action": "read"},
            {"name": "quality:firstInspection:write", "display_name": "配置首检", "resource": "quality:firstInspection", "action": "write"},
            {"name": "quality:firstInspection:release", "display_name": "首检放行", "resource": "quality:firstInspection", "action": "release"},
            {"name": "quality:testData:read", "display_name": "查看测试数据", "resource": "quality:testData", "action": "read"},
            {"name": "quality:testData:write", "display_name": "配置测试数据", "resource": "quality:testData", "action": "write"},
            # 系统
            {"name": "system:users:read", "display_name": "查看用户", "resource": "system:users", "action": "read"},
            {"name": "system:users:write", "display_name": "编辑用户", "resource": "system:users", "action": "write"},
            {"name": "system:roles:read", "display_name": "查看角色", "resource": "system:roles", "action": "read"},
            {"name": "system:roles:write", "display_name": "编辑角色", "resource": "system:roles", "action": "write"},
        ]

        for p in default_perms:
            if self.perm_repo.get_by_name(p["name"]) is None:
                self.create_permission(PermissionCreate(**p))

        # 创建默认角色
        default_roles = [
            {"name": "admin", "display_name": "系统管理员", "description": "拥有所有权限", "is_system": True, "permissions": {"resources": {"*": ["*"]}}},
            {"name": "supervisor", "display_name": "班组长", "description": "管理产线日常生产", "is_system": True, "permissions": {"resources": {
                "masterdata:*": ["read"],
                "production:*": ["*"],
                "trace:*": ["*"],
                "quality:*": ["*"],
                "system:users": ["read"],
            }}},
            {"name": "operator", "display_name": "操作员", "description": "工位作业", "is_system": True, "permissions": {"resources": {
                "production:station": ["use"],
                "production:wip": ["read"],
            }}},
            {"name": "viewer", "display_name": "查看者", "description": "只读权限", "is_system": True, "permissions": {"resources": {
                "masterdata:*": ["read"],
                "production:*": ["read"],
                "trace:*": ["read"],
                "quality:*": ["read"],
            }}},
        ]

        for r in default_roles:
            if self.role_repo.get_by_name(r["name"]) is None:
                role = Role(
                    name=r["name"],
                    display_name=r["display_name"],
                    description=r["description"],
                    is_system=r["is_system"],
                    permissions=r["permissions"],
                )
                self.role_repo.add(role)

        self.db.flush()

    def ensure_admin_user(self, initial_password: str | None = None) -> None:
        """确保存在管理员用户；首次创建必须显式提供初始密码。"""
        admin_role = self.role_repo.get_by_name("admin")
        if admin_role is None:
            self.initialize_default_roles()
            admin_role = self.role_repo.get_by_name("admin")

        if self.user_repo.get_by_username("admin") is None:
            if not initial_password:
                raise ValueError(
                    "不存在管理员账户，请设置 ADMIN_INITIAL_PASSWORD 后启动"
                )
            admin = User(
                username="admin",
                password_hash=hash_password(initial_password),
                display_name="系统管理员",
                role_id=admin_role.id,
            )
            self.user_repo.add(admin)
            self.db.flush()
