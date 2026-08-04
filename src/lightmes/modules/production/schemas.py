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


class ComponentInput(BaseModel):
    component_product_id: int
    component_sn: str | None = None
    component_batch_no: str | None = None
    qty: float = 1


class StationPassInput(BaseModel):
    station_id: int
    work_order_code: str | None = None
    sn: str | None = None
    operator_id: int | None = None
    components: list[ComponentInput] = []


class StepInfo(BaseModel):
    seq: int
    name: str
    station_id: int


class StationPassResult(BaseModel):
    sn: str
    passed_step: StepInfo
    next_step: StepInfo | None
    is_finished: bool
    work_order_status: str
    bound_count: int = 0


class WipItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sn: str
    status: str
    current_step_seq: int
    current_station_id: int | None
