from datetime import datetime

from pydantic import BaseModel


class ComponentBind(BaseModel):
    component_product_id: int
    component_sn: str | None = None
    component_batch_no: str | None = None
    qty: float = 1


class BindView(BaseModel):
    component_product_id: int
    component_type: str
    component_ref: str
    qty: float
    status: str


class PassView(BaseModel):
    routing_step_id: int
    station_id: int
    result: str
    pass_time: datetime


class GenealogyView(BaseModel):
    sn: str
    components: list[BindView]


class HistoryView(BaseModel):
    sn: str
    passes: list[PassView]
    components: list[BindView]


class ParentRef(BaseModel):
    parent_sn_id: int
    component_ref: str
    status: str
