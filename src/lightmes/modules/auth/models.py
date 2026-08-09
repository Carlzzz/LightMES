from sqlalchemy import ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from lightmes.shared.base import Base, TimestampMixin


class Role(Base, TimestampMixin):
    """角色表"""
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    display_name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    is_system: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    permissions: Mapped[dict | None] = mapped_column(JSON, default=None)

    users: Mapped[list["User"]] = relationship("User", back_populates="role_obj")


class User(Base, TimestampMixin):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str] = mapped_column()
    display_name: Mapped[str] = mapped_column()
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"), default=None)
    is_active: Mapped[bool] = mapped_column(default=True)

    role_obj: Mapped[Role | None] = relationship("Role", back_populates="users")


class Permission(Base, TimestampMixin):
    """权限定义表"""
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    display_name: Mapped[str] = mapped_column()
    resource: Mapped[str] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(index=True)
    description: Mapped[str | None] = mapped_column(default=None)


class RolePermission(Base, TimestampMixin):
    """角色-权限关联表"""
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"), index=True)
    # 可选的规则配置（字段级权限、数据范围等）
    rules: Mapped[dict | None] = mapped_column(JSON, default=None)
