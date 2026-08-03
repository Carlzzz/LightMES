from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SnRuleCreate(BaseModel):
    code: str
    name: str
    pattern: str
    seq_reset: str = "never"
    product_id: int | None = None


class SnRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    pattern: str
    seq_reset: str
    product_id: int | None


class WorkOrderCreate(BaseModel):
    code: str
    product_id: int
    routing_id: int
    qty: int
    sn_rule_id: int | None = None


class WorkOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    product_id: int
    routing_id: int
    sn_rule_id: int | None
    qty: int
    status: str
    source: str
    produced_qty: int
    planned_start: datetime | None
    planned_end: datetime | None
