from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["ERP Sync"])
    scopes: list[str] = Field(default=["read"], examples=[["read", "write"]])
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    """API Key 列表项 — 不含 key_hash，不含完整 key。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(BaseModel):
    """POST 创建后的响应 — full_key 仅此一次返回。"""
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    full_key: str  # 仅此一次
    created_at: datetime


class WorkOrderReadV1(BaseModel):
    """WorkOrder for API v1 — no internal fields like process_snapshot."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    product_id: int
    routing_id: int
    line_id: int
    sn_rule_id: int | None
    qty: int
    status: str
    source: str
    produced_qty: int
    planned_start: datetime | None
    planned_end: datetime | None
    priority: int
    created_at: datetime


class WorkOrderCreateV1(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, examples=["WO-2026-001"])
    product_id: int
    routing_id: int
    line_id: int
    sn_rule_id: int | None = None
    qty: int = Field(..., gt=0)
    priority: int = Field(default=5, ge=1, le=9)


class WorkOrderPriorityPatch(BaseModel):
    priority: int = Field(..., ge=1, le=9)


class SerialUnitReadV1(BaseModel):
    """SerialUnit for API v1."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    sn: str
    work_order_id: int
    product_id: int
    status: str
    current_operation_seq: int
    is_counted: bool
    carrier_code: str | None
    created_at: datetime


class DefectReadV1(BaseModel):
    """Defect for API v1."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    defect_type_code: str
    defect_type_name: str
    severity: str
    serial_unit_id: int
    work_order_id: int
    operation_id: int | None
    work_station_id: int | None
    position: str | None
    handling_status: str  # pending / rework / scrap / concession
    discovered_by: int | None
    discovered_at: datetime
    handled_by: int | None
    handled_at: datetime | None
    handling_remark: str | None
    remark: str | None


class DefectTypeReadV1(BaseModel):
    """Defect type for API v1."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    category: str | None
    severity: str
    description: str | None
    is_active: bool
