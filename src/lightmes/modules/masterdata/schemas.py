from pydantic import BaseModel, ConfigDict


class ProductCreate(BaseModel):
    code: str
    name: str
    type: str
    unit: str = "pcs"
    track_mode: str = "none"
    spec: str | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    type: str
    unit: str
    track_mode: str
    spec: str | None
