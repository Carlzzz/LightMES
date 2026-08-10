from __future__ import annotations

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


class FirstInspectionInput(BaseModel):
    check_results: list[FirstInspectionCheckResultInput]
    remark: str | None = None


class OperationPassInput(BaseModel):
    work_station_id: int
    work_order_code: str | None = None
    sn: str | None = None
    operator_id: int | None = None
    components: list[ComponentInput] = []
    params: list[ParamInput] = []
    first_inspection: FirstInspectionInput | None = None


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
    next_op_can_continue_here: bool = False


class OperationSkipInput(BaseModel):
    work_station_id: int
    sn: str | None = None
    work_order_code: str | None = None
    operator_id: int | None = None
    reason: str  # 必填


class OperationSkipResult(BaseModel):
    sn: str
    skipped_op: OpInfo
    next_op: OpInfo | None
    is_finished: bool  # 恒 False（末工序不可跳）
    work_order_status: str
    next_op_can_continue_here: bool = False


class WipItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sn: str
    status: str
    current_operation_seq: int


class StationOpView(BaseModel):
    operation_id: int  # 新增：Layer 2 过滤用
    seq: int
    name: str
    code: str
    work_station_id: int
    status: str  # "done" | "current" | "future" | "skipped"
    allowed_work_stations: list[str] = []


class StationComponentView(BaseModel):
    component_product_id: int
    component_code: str
    component_name: str
    qty: float
    track_mode: str = "none"


class FirstInspectionStationView(BaseModel):
    needs_inspection: bool
    trigger_reason: str | None = None
    config_id: int | None = None
    config_name: str | None = None
    check_items: list[FirstInspectionCheckItemRead] = []
    record_id: int | None = None
    record_status: str | None = None


class TestDataStationView(BaseModel):
    needs_test_data: bool
    template_id: int | None = None
    template_name: str | None = None
    fields: list[TestDataFieldRead] = []


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
    station_operations: list[StationOpView] = []  # 新增：Layer 2（本站 allowed 子集）
    current_op: StationOpView | None
    components: list[StationComponentView]
    sop_text: str | None = None
    sop_url: str | None = None
    first_inspection: FirstInspectionStationView | None = None
    test_data: TestDataStationView | None = None


class CarrierBindInput(BaseModel):
    work_order_id: int
    carrier_code: str
    work_station_id: int
    components: list[ComponentInput] = []
    params: list[ParamInput] = []


class CarrierUnbindInput(BaseModel):
    scan: str


# ========== First Inspection Schemas ==========

class FirstInspectionCheckItemCreate(BaseModel):
    seq: int
    name: str
    description: str | None = None
    check_type: str = "boolean"
    unit: str | None = None
    standard_value: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    is_mandatory: bool = True


class FirstInspectionCheckItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    config_id: int
    seq: int
    name: str
    description: str | None
    check_type: str
    unit: str | None
    standard_value: str | None
    min_value: float | None
    max_value: float | None
    is_mandatory: bool


class FirstInspectionConfigCreate(BaseModel):
    operation_id: int
    work_station_id: int | None = None
    name: str
    is_enabled: bool = True
    trigger_new_order: bool = True
    trigger_material_change: bool = False
    trigger_tooling_change: bool = False
    trigger_param_revision: bool = False
    trigger_abnormal_restart: bool = False
    trigger_shift_handover: bool = False
    trigger_cold_start: bool = False
    trigger_previous_failed: bool = True
    sample_size: int = 1
    require_authorization: bool = True
    authorized_roles: list[str] | None = None
    quarantine_on_fail: bool = True
    check_items: list[FirstInspectionCheckItemCreate] = []


class FirstInspectionConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    operation_id: int
    work_station_id: int | None
    name: str
    is_enabled: bool
    trigger_new_order: bool
    trigger_material_change: bool
    trigger_tooling_change: bool
    trigger_param_revision: bool
    trigger_abnormal_restart: bool
    trigger_shift_handover: bool
    trigger_cold_start: bool
    trigger_previous_failed: bool
    sample_size: int
    require_authorization: bool
    authorized_roles: list[str] | None
    quarantine_on_fail: bool


class FirstInspectionCheckResultInput(BaseModel):
    check_item_id: int
    result_type: str
    boolean_value: bool | None = None
    numeric_value: float | None = None
    text_value: str | None = None
    remark: str | None = None


class FirstInspectionSubmitInput(BaseModel):
    record_id: int
    check_results: list[FirstInspectionCheckResultInput]
    remark: str | None = None


class FirstInspectionReleaseInput(BaseModel):
    record_id: int
    release_remark: str | None = None


class FirstInspectionCheckResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    record_id: int
    check_item_id: int
    check_item_name: str
    result_type: str
    boolean_value: bool | None
    numeric_value: float | None
    text_value: str | None
    is_pass: bool
    remark: str | None


class FirstInspectionRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    config_id: int
    work_order_id: int
    operation_id: int
    work_station_id: int
    serial_unit_id: int | None
    trigger_reason: str
    trigger_detail: str | None
    inspector_id: int
    inspected_at: datetime
    status: str
    remark: str | None
    released_by_id: int | None
    released_at: datetime | None
    release_remark: str | None


class FirstInspectionStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    work_order_id: int
    operation_id: int
    last_inspection_record_id: int | None
    last_passed_at: datetime | None
    current_material_batch: str | None
    current_tooling_id: str | None
    current_param_version: str | None
    last_produced_at: datetime | None
    last_shift_date: str | None
    is_abnormal_state: bool


# ========== Test Data Schemas ==========

class TestDataFieldCreate(BaseModel):
    seq: int
    code: str
    name: str
    field_type: str = "numeric"
    unit: str | None = None
    is_required: bool = True
    standard_value: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    options: list[str] | None = None
    display_group: str | None = None


class TestDataFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    template_id: int
    seq: int
    code: str
    name: str
    field_type: str
    unit: str | None
    is_required: bool
    standard_value: str | None
    min_value: float | None
    max_value: float | None
    options: list[str] | None
    display_group: str | None


class TestDataTemplateCreate(BaseModel):
    operation_id: int
    work_station_id: int | None = None
    name: str
    description: str | None = None
    is_enabled: bool = True
    version: str = "1"
    fields: list[TestDataFieldCreate] = []


class TestDataTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    operation_id: int
    work_station_id: int | None
    name: str
    description: str | None
    is_enabled: bool
    version: str


class TestDataValueInput(BaseModel):
    field_id: int
    value_type: str
    numeric_value: float | None = None
    boolean_value: bool | None = None
    text_value: str | None = None


class TestDataRecordSubmitInput(BaseModel):
    operation_record_id: int
    values: list[TestDataValueInput]
    remark: str | None = None


class TestDataValueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    record_id: int
    field_id: int
    field_code: str
    field_name: str
    value_type: str
    numeric_value: float | None
    boolean_value: bool | None
    text_value: str | None
    is_pass: bool | None
    out_of_spec: bool
    remark: str | None


class TestDataRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    template_id: int
    operation_record_id: int
    serial_unit_id: int
    work_order_id: int
    operation_id: int
    work_station_id: int
    operator_id: int
    overall_result: str
    test_started_at: datetime | None
    test_completed_at: datetime | None
    remark: str | None
