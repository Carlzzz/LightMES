from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, text
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


class ApiKey(Base, TimestampMixin):
    """API 密钥表（Bearer token 凭据）"""
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None)
