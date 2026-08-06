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
    line_id: int
    qty: int
    sn_rule_id: int | None = None


class WorkOrderRead(BaseModel):
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


class ComponentInput(BaseModel):
    component_product_id: int
    component_sn: str | None = None
    component_batch_no: str | None = None
    qty: float = 1


class ParamInput(BaseModel):
    param_key: str
    param_value: str
    unit: str | None = None


class OperationPassInput(BaseModel):
    work_station_id: int
    work_order_code: str | None = None
    sn: str | None = None
    operator_id: int | None = None
    components: list[ComponentInput] = []
    params: list[ParamInput] = []


class OpInfo(BaseModel):
    seq: int
    name: str
    work_station_id: int


class OperationPassResult(BaseModel):
    sn: str
    passed_op: OpInfo
    next_op: OpInfo | None
    is_finished: bool
    work_order_status: str
    bound_count: int = 0
    param_count: int = 0


class WipItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sn: str
    status: str
    current_operation_seq: int


class StationOpView(BaseModel):
    seq: int
    name: str
    code: str
    work_station_id: int
    status: str  # "done" | "current" | "future"


class StationComponentView(BaseModel):
    component_product_id: int
    component_code: str
    component_name: str
    qty: float


class StationView(BaseModel):
    sn: str
    work_order_code: str
    product_code: str
    product_name: str
    operator_name: str
    operator_skill_level: int | None
    required_level: int | None
    skill_ok: bool
    is_off_station: bool
    is_finished: bool
    operations: list[StationOpView]
    current_op: StationOpView | None
    components: list[StationComponentView]
    sop_placeholder: bool = True
