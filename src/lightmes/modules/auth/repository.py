from sqlalchemy import select, and_
from sqlalchemy.orm import Session, joinedload
from lightmes.modules.auth.models import User, Role, Permission, RolePermission


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return self.db.execute(stmt).scalar_one_or_none()

    def add(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()
        return user

    def get(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def get_with_role(self, user_id: int) -> User | None:
        stmt = select(User).where(User.id == user_id).options(joinedload(User.role_obj))
        return self.db.execute(stmt).scalar_one_or_none()

    def list_all(self) -> list[User]:
        return list(self.db.execute(select(User)).scalars().all())

    def list_all_with_roles(self) -> list[User]:
        stmt = select(User).options(joinedload(User.role_obj))
        return list(self.db.execute(stmt).scalars().all())


class RoleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, role: Role) -> Role:
        self.db.add(role)
        self.db.flush()
        return role

    def get(self, role_id: int) -> Role | None:
        return self.db.get(Role, role_id)

    def get_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_all(self) -> list[Role]:
        return list(self.db.execute(select(Role)).scalars().all())

    def list_active(self) -> list[Role]:
        return list(self.db.execute(select(Role).where(Role.is_active == True)).scalars().all())


class PermissionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, perm: Permission) -> Permission:
        self.db.add(perm)
        self.db.flush()
        return perm

    def get(self, perm_id: int) -> Permission | None:
        return self.db.get(Permission, perm_id)

    def get_by_name(self, name: str) -> Permission | None:
        stmt = select(Permission).where(Permission.name == name)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_all(self) -> list[Permission]:
        return list(self.db.execute(select(Permission)).scalars().all())

    def list_by_resource(self, resource: str) -> list[Permission]:
        return list(self.db.execute(select(Permission).where(Permission.resource == resource)).scalars().all())


class RolePermissionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, rp: RolePermission) -> RolePermission:
        self.db.add(rp)
        self.db.flush()
        return rp

    def get_by_role_and_permission(self, role_id: int, permission_id: int) -> RolePermission | None:
        stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission_id
            )
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_by_role(self, role_id: int) -> list[RolePermission]:
        return list(self.db.execute(select(RolePermission).where(RolePermission.role_id == role_id)).scalars().all())

    def delete_by_role(self, role_id: int) -> None:
        self.db.execute(RolePermission.__table__.delete().where(RolePermission.role_id == role_id))
        self.db.flush()

    def delete(self, rp_id: int) -> None:
        rp = self.db.get(RolePermission, rp_id)
        if rp:
            self.db.delete(rp)
            self.db.flush()
