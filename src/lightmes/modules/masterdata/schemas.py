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


class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    is_mandatory: bool = True


class OperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    routing_id: int
    seq: int
    code: str
    name: str
    default_work_station_id: int
    is_mandatory: bool


class RoutingCreate(BaseModel):
    code: str
    name: str
    product_id: int
    version: str = "1"
    operations: list[OperationCreate]


class RoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    product_id: int
    version: str
    status: str
    operations: list[OperationRead]


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
