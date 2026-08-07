from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "supervisor", "operator", "viewer"]


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role: Role = "operator"


class LoginResponse(BaseModel):
    username: str
    display_name: str
    role: Role = "operator"
