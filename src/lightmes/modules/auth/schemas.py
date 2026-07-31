from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "operator"


class LoginResponse(BaseModel):
    username: str
    display_name: str
