from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ProductCreate(BaseModel):
    code: str
    name: str
    type: str
    unit: str = "pcs"
    track_mode: str = "none"
    spec: str | None = None


class ProductUpsert(BaseModel):
    erp_ref: str
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
    source: str
    erp_ref: str | None
    synced_at: datetime | None


class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int]  # 新增：至少 1 个；必须含 default_work_station_id
    is_mandatory: bool = True
    required_skill_id: int | None = None
    required_level: int | None = None


class OperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    routing_id: int
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int] = []
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
    source: str
    erp_ref: str | None
    synced_at: datetime | None
    operations: list[OperationRead]


class BomItemCreate(BaseModel):
    component_product_id: int
    qty: float = 1
    consume_at_operation_seq: int | None = None


class BomCreate(BaseModel):
    product_id: int
    version: str = "1"
    items: list[BomItemCreate]


class BomItemUpsert(BaseModel):
    component_code: str
    qty: float = 1
    consume_at_operation_seq: int | None = None


class BomUpsert(BaseModel):
    erp_ref: str
    product_code: str
    items: list[BomItemUpsert]


class BomItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    component_product_id: int
    qty: float
    track_mode: str
    consume_at_operation_seq: int | None


class BomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    version: str
    status: str
    source: str
    erp_ref: str | None
    synced_at: datetime | None
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


class SkillCreate(BaseModel):
    code: str
    name: str
    max_level: int
    description: str | None = None


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    max_level: int
    description: str | None


class OperatorSkillCreate(BaseModel):
    user_id: int
    skill_id: int
    level: int


class OperatorSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    skill_id: int
    level: int
