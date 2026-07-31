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


class StationCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    location: str | None = None


class StationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None
    location: str | None
    is_active: bool


class RoutingStepCreate(BaseModel):
    seq: int
    station_id: int
    name: str
    is_mandatory: bool = True


class RoutingCreate(BaseModel):
    code: str
    name: str
    product_id: int
    version: str = "1"
    steps: list[RoutingStepCreate]


class RoutingStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    seq: int
    station_id: int
    name: str
    is_mandatory: bool


class RoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    product_id: int
    version: str
    status: str
    steps: list[RoutingStepRead]
