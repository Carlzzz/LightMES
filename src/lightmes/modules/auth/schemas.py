from typing import Literal, Any

from pydantic import BaseModel, ConfigDict

RoleType = Literal["admin", "supervisor", "operator", "viewer"]


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role_id: int | None = None


class UserUpdate(BaseModel):
    display_name: str | None = None
    role_id: int | None = None
    is_active: bool | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role_id: int | None
    is_active: bool


class LoginResponse(BaseModel):
    username: str
    display_name: str
    role_id: int | None
    permissions: list[str] = []


class RoleCreate(BaseModel):
    name: str
    display_name: str
    description: str | None = None


class RoleUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    permissions: dict | None = None


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    display_name: str
    description: str | None
    is_system: bool
    is_active: bool
    permissions: dict | None


class PermissionCreate(BaseModel):
    name: str
    display_name: str
    resource: str
    action: str
    description: str | None = None


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    display_name: str
    resource: str
    action: str
    description: str | None
