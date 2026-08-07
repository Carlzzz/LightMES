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


class OpRecordView(BaseModel):
    operation_id: int
    operation_name: str = ""
    operation_seq: int = 0
    work_station_id: int
    work_station_name: str = ""
    line_id: int
    result: str
    end_time: datetime


class ParamView(BaseModel):
    param_key: str
    param_value: str
    unit: str | None = None
    source: str
    recorded_at: datetime


class GenealogyView(BaseModel):
    sn: str
    components: list[BindView]


class HistoryView(BaseModel):
    sn: str
    records: list[OpRecordView]
    components: list[BindView]
    params: list[ParamView]


class ParentRef(BaseModel):
    parent_sn_id: int
    parent_sn: str = ""
    component_ref: str
    status: str
