from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "operator"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
