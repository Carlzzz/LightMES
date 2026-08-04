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


class BomItemCreate(BaseModel):
    component_product_id: int
    qty: float = 1


class BomCreate(BaseModel):
    product_id: int
    version: str = "1"
    items: list[BomItemCreate]


class BomItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    component_product_id: int
    qty: float
    track_mode: str


class BomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    version: str
    status: str
    items: list[BomItemRead]


class LineCreate(BaseModel):
    code: str
    name: str
    description: str | None = None


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None
    is_active: bool


class WorkStationCreate(BaseModel):
    code: str
    name: str
    line_id: int
    seq: int
    description: str | None = None


class WorkStationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    line_id: int
    seq: int
    description: str | None
    is_active: bool
