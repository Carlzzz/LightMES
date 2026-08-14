from datetime import datetime
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from lightmes.shared.base import Base, TimestampMixin


class SnRule(Base, TimestampMixin):
    __tablename__ = "sn_rules"
    __table_args__ = (
        CheckConstraint(
            "seq_reset IN ('never', 'daily', 'monthly')",
            name="ck_sn_rules_seq_reset",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), default=None
    )
    pattern: Mapped[str] = mapped_column()
    seq_reset: Mapped[str] = mapped_column(default="never")  # never/daily/monthly
    current_seq: Mapped[int] = mapped_column(default=0)
    seq_period_key: Mapped[str | None] = mapped_column(default=None)


class WorkOrder(Base, TimestampMixin):
    __tablename__ = "work_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'released', 'in_process', 'completed', 'cancelled')",
            name="ck_work_orders_status",
        ),
        CheckConstraint("qty > 0", name="ck_work_orders_qty_positive"),
        CheckConstraint("produced_qty >= 0", name="ck_work_orders_produced_qty_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    sn_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("sn_rules.id"), default=None
    )
    qty: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")
    source: Mapped[str] = mapped_column(default="manual")
    produced_qty: Mapped[int] = mapped_column(default=0)
    planned_start: Mapped[datetime | None] = mapped_column(default=None)
    planned_end: Mapped[datetime | None] = mapped_column(default=None)
    priority: Mapped[int] = mapped_column(default=5)
    process_snapshot: Mapped[dict | None] = mapped_column(JSON, default=None)
    custom_fields: Mapped[dict | None] = mapped_column(JSON, default=None)


class SerialUnit(Base, TimestampMixin):
    __tablename__ = "serial_units"
    __table_args__ = (
        Index(
            "uq_active_carrier", "carrier_code",
            unique=True, postgresql_where=text("carrier_code IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('pending', 'in_process', 'reworking', 'quarantined', 'finished', 'scrapped')",
            name="ck_serial_units_status",
        ),
        CheckConstraint("current_operation_seq >= 0", name="ck_serial_units_current_seq_nonnegative"),
        CheckConstraint("version >= 0", name="ck_serial_units_version_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sn: Mapped[str] = mapped_column(unique=True, index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(default="in_process")
    current_operation_seq: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=0)
    # 是否已计入工单完工数；返工再完工不重复计数（一个物理 SN 只计一次）
    is_counted: Mapped[bool] = mapped_column(default=False, server_default="false")
    carrier_code: Mapped[str | None] = mapped_column(default=None)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id"), default=None, index=True
    )
    # 返工时设定的预期 re-pass 站位；首次 re-pass 后清 null（service 层保证）
    rework_target_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), default=None
    )


class OperationRecord(Base, TimestampMixin):
    __tablename__ = "operation_records"
    __table_args__ = (
        CheckConstraint(
            "result IN ('pass', 'fail', 'skip')",
            name="ck_operation_records_result",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id"))
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    result: Mapped[str] = mapped_column(default="pass")
    remark: Mapped[str | None] = mapped_column(default=None)


class Batch(Base, TimestampMixin):
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("work_order_id", "batch_number", name="uq_batch_work_order_number"),
        CheckConstraint(
            "status IN ('pending', 'in_process', 'done', 'cancelled')",
            name="ck_batches_status",
        ),
        CheckConstraint("target_qty > 0", name="ck_batches_target_qty_positive"),
        CheckConstraint("produced_qty >= 0", name="ck_batches_produced_qty_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    batch_number: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(default="pending")
    target_qty: Mapped[int] = mapped_column()
    produced_qty: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MaterialLot(Base, TimestampMixin):
    __tablename__ = "material_lots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'quarantined', 'released', 'consumed', 'rejected')",
            name="ck_material_lots_status",
        ),
        CheckConstraint("quantity >= 0", name="ck_material_lots_quantity_nonnegative"),
        CheckConstraint("available_quantity >= 0", name="ck_material_lots_available_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    available_quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    status: Mapped[str] = mapped_column(default="received")
    supplier_lot: Mapped[str | None] = mapped_column(default=None)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    custom_fields: Mapped[dict | None] = mapped_column(JSON, default=None)


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('receive', 'release', 'consume', 'return', 'adjustment')",
            name="ck_stock_movements_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    material_lot_id: Mapped[int] = mapped_column(ForeignKey("material_lots.id"), index=True)
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    source_type: Mapped[str | None] = mapped_column(String(50), default=None)
    source_id: Mapped[int | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(default=None)


class BatchMaterialConsumption(Base, TimestampMixin):
    __tablename__ = "batch_material_consumptions"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_batch_material_consumption_quantity_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    material_lot_id: Mapped[int] = mapped_column(ForeignKey("material_lots.id"), index=True)
    operation_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("operation_records.id"), default=None, index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=0)


class OperationParam(Base, TimestampMixin):
    __tablename__ = "operation_params"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_record_id: Mapped[int] = mapped_column(
        ForeignKey("operation_records.id")
    )
    param_key: Mapped[str] = mapped_column()
    param_value: Mapped[str] = mapped_column()
    unit: Mapped[str | None] = mapped_column(default=None)
    source: Mapped[str] = mapped_column(default="manual")  # manual/auto
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CarrierBinding(Base, TimestampMixin):
    __tablename__ = "carrier_binding"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    carrier_code: Mapped[str] = mapped_column()
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    unbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    unbound_reason: Mapped[str | None] = mapped_column(default=None)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None)


class FirstInspectionConfig(Base, TimestampMixin):
    """首检配置：定义哪些工序/站位需要首检及触发条件"""
    __tablename__ = "first_inspection_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), index=True)
    work_station_id: Mapped[int | None] = mapped_column(ForeignKey("work_stations.id"), default=None, index=True)
    is_enabled: Mapped[bool] = mapped_column(default=True)
    name: Mapped[str] = mapped_column()

    # 触发条件配置（各条件可单独启用）
    trigger_new_order: Mapped[bool] = mapped_column(default=True)  # 新工单开始
    trigger_material_change: Mapped[bool] = mapped_column(default=False)  # 物料/批次变更
    trigger_tooling_change: Mapped[bool] = mapped_column(default=False)  # 工装/模具变更
    trigger_tool_change: Mapped[bool] = mapped_column(default=False)  # 工装变更（别名）
    trigger_param_revision: Mapped[bool] = mapped_column(default=False)  # 参数修订
    trigger_abnormal_restart: Mapped[bool] = mapped_column(default=False)  # 异常重启
    trigger_shift_handover: Mapped[bool] = mapped_column(default=False)  # 交接班
    trigger_cold_start: Mapped[bool] = mapped_column(default=False)  # 冷启动（停产≥4小时）
    trigger_previous_failed: Mapped[bool] = mapped_column(default=True)  # 前序失败需首件复检

    # 首检策略
    sample_size: Mapped[int] = mapped_column(default=1)  # 首检数量（一般1件）
    require_authorization: Mapped[bool] = mapped_column(default=True)  # 是否需要授权人员放行
    authorized_roles: Mapped[list[str] | None] = mapped_column(JSON, default=None)  # 可放行的角色列表
    quarantine_on_fail: Mapped[bool] = mapped_column(default=True)  # 失败时是否批次隔离


class FirstInspectionCheckItem(Base, TimestampMixin):
    """首检检查项配置"""
    __tablename__ = "first_inspection_check_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("first_inspection_configs.id"), index=True)
    seq: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    check_type: Mapped[str] = mapped_column(default="boolean")  # boolean/value/text
    unit: Mapped[str | None] = mapped_column(default=None)
    standard_value: Mapped[str | None] = mapped_column(default=None)
    min_value: Mapped[float | None] = mapped_column(Numeric(12, 3), default=None)
    max_value: Mapped[float | None] = mapped_column(Numeric(12, 3), default=None)
    is_mandatory: Mapped[bool] = mapped_column(default=True)


class FirstInspectionRecord(Base, TimestampMixin):
    """首检记录"""
    __tablename__ = "first_inspection_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'passed', 'failed', 'waived')",
            name="ck_first_inspection_records_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("first_inspection_configs.id"))
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
    serial_unit_id: Mapped[int | None] = mapped_column(ForeignKey("serial_units.id"), default=None, index=True)

    trigger_reason: Mapped[str] = mapped_column()  # 触发原因（new_order/material_change等）
    trigger_detail: Mapped[str | None] = mapped_column(default=None)  # 触发详情

    inspector_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(default="pending")  # pending/passed/failed/waived
    remark: Mapped[str | None] = mapped_column(default=None)

    # 授权放行
    released_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    release_remark: Mapped[str | None] = mapped_column(default=None)


class FirstInspectionCheckResult(Base, TimestampMixin):
    """首检检查项结果"""
    __tablename__ = "first_inspection_check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("first_inspection_records.id"), index=True)
    check_item_id: Mapped[int] = mapped_column(ForeignKey("first_inspection_check_items.id"))
    check_item_name: Mapped[str] = mapped_column()  # 快照检查项名称
    result_type: Mapped[str] = mapped_column()  # boolean/value/text
    boolean_value: Mapped[bool | None] = mapped_column(default=None)
    numeric_value: Mapped[float | None] = mapped_column(Numeric(12, 3), default=None)
    text_value: Mapped[str | None] = mapped_column(default=None)
    is_pass: Mapped[bool] = mapped_column()
    remark: Mapped[str | None] = mapped_column(default=None)


class FirstInspectionState(Base, TimestampMixin):
    """首检状态跟踪：记录当前工序/工单的首检状态，避免重复触发"""
    __tablename__ = "first_inspection_states"
    __table_args__ = (
        Index("uq_wo_op_state", "work_order_id", "operation_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), index=True)
    last_inspection_record_id: Mapped[int | None] = mapped_column(ForeignKey("first_inspection_records.id"), default=None)
    last_passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # 状态跟踪用于触发判断
    current_material_batch: Mapped[str | None] = mapped_column(default=None)
    current_tooling_id: Mapped[str | None] = mapped_column(default=None)
    current_param_version: Mapped[str | None] = mapped_column(default=None)
    last_produced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_shift_date: Mapped[str | None] = mapped_column(default=None)
    is_abnormal_state: Mapped[bool] = mapped_column(default=False)


class TestDataTemplate(Base, TimestampMixin):
    """测试数据模板：定义测试站位需要采集的参数"""
    __tablename__ = "test_data_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), index=True)
    work_station_id: Mapped[int | None] = mapped_column(ForeignKey("work_stations.id"), default=None, index=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(default=None)
    is_enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[str] = mapped_column(default="1")


class TestDataField(Base, TimestampMixin):
    """测试数据字段定义"""
    __tablename__ = "test_data_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("test_data_templates.id"), index=True)
    seq: Mapped[int] = mapped_column()
    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    field_type: Mapped[str] = mapped_column(default="numeric")  # numeric/boolean/text/select
    unit: Mapped[str | None] = mapped_column(default=None)
    is_required: Mapped[bool] = mapped_column(default=True)
    standard_value: Mapped[str | None] = mapped_column(default=None)
    min_value: Mapped[float | None] = mapped_column(Numeric(12, 3), default=None)
    max_value: Mapped[float | None] = mapped_column(Numeric(12, 3), default=None)
    options: Mapped[list[str] | None] = mapped_column(JSON, default=None)  # select类型的选项
    display_group: Mapped[str | None] = mapped_column(default=None)


class TestDataRecord(Base, TimestampMixin):
    """测试数据记录"""
    __tablename__ = "test_data_records"
    __table_args__ = (
        CheckConstraint(
            "overall_result IN ('pending', 'passed', 'failed')",
            name="ck_test_data_records_overall_result",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("test_data_templates.id"))
    operation_record_id: Mapped[int] = mapped_column(ForeignKey("operation_records.id"), index=True)
    serial_unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"), index=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    work_station_id: Mapped[int] = mapped_column(ForeignKey("work_stations.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    overall_result: Mapped[str] = mapped_column(default="pending")  # pending/passed/failed
    test_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    test_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    remark: Mapped[str | None] = mapped_column(default=None)


class TestDataValue(Base, TimestampMixin):
    """测试数据明细值"""
    __tablename__ = "test_data_values"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("test_data_records.id"), index=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("test_data_fields.id"))
    field_code: Mapped[str] = mapped_column()
    field_name: Mapped[str] = mapped_column()

    value_type: Mapped[str] = mapped_column()
    numeric_value: Mapped[float | None] = mapped_column(Numeric(12, 3), default=None)
    boolean_value: Mapped[bool | None] = mapped_column(default=None)
    text_value: Mapped[str | None] = mapped_column(default=None)

    is_pass: Mapped[bool | None] = mapped_column(default=None)
    out_of_spec: Mapped[bool] = mapped_column(default=False)
    remark: Mapped[str | None] = mapped_column(default=None)


class DefectType(Base, TimestampMixin):
    """缺陷类型主数据"""
    __tablename__ = "defect_types"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical', 'major', 'minor')",
            name="ck_defect_types_severity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column()
    category: Mapped[str | None] = mapped_column(default=None)  # 外观/尺寸/功能/其他
    severity: Mapped[str] = mapped_column(default="major")  # critical/major/minor
    description: Mapped[str | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class DefectRecord(Base, TimestampMixin):
    """缺陷记录（实例）"""
    __tablename__ = "defect_records"
    __table_args__ = (
        CheckConstraint(
            "handling_status IN ('pending', 'rework', 'scrap', 'concession')",
            name="ck_defect_records_handling_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    defect_type_id: Mapped[int] = mapped_column(
        ForeignKey("defect_types.id"), index=True)
    defect_type_code: Mapped[str] = mapped_column()  # 快照
    defect_type_name: Mapped[str] = mapped_column()  # 快照
    severity: Mapped[str] = mapped_column()  # 快照（登记时刻）
    serial_unit_id: Mapped[int] = mapped_column(
        ForeignKey("serial_units.id"), index=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("operations.id"), default=None)
    work_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_stations.id"), default=None)
    position: Mapped[str | None] = mapped_column(default=None)
    discovered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    handling_status: Mapped[str] = mapped_column(default="pending")  # pending/rework/scrap/concession
    handled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None)
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    handling_remark: Mapped[str | None] = mapped_column(default=None)
    remark: Mapped[str | None] = mapped_column(default=None)


class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"
    __table_args__ = (
        UniqueConstraint("code", name="uq_shift_code"),
        CheckConstraint(
            "start_time ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]$'",
            name="ck_shift_start_time_hhmm",
        ),
        CheckConstraint(
            "end_time ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]$'",
            name="ck_shift_end_time_hhmm",
        ),
        CheckConstraint(
            "json_typeof(days_of_week) = 'array' OR days_of_week IS NULL",
            name="ck_shift_days_of_week_array_or_null",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    start_time: Mapped[str] = mapped_column()  # "HH:MM"
    end_time: Mapped[str] = mapped_column()    # "HH:MM"（end < start 表示跨夜）
    # JSON(none_as_null=True): Python None → SQL NULL（满足 ck_shift_days_of_week_array_or_null
    # 约束）。默认 JSON 把 None 序列化为 JSON literal 'null'，违反 IS NULL 分支。
    days_of_week: Mapped[list | None] = mapped_column(JSON(none_as_null=True), default=None)
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("lines.id"), default=None)  # NULL = 全局班次
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(default=0)


class ScheduleChangeLog(Base, TimestampMixin):
    __tablename__ = "schedule_change_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('schedule', 'unschedule', 'move', 'undo')",
            name="ck_schedule_change_log_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("work_orders.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None)
    action: Mapped[str] = mapped_column()  # schedule / unschedule / move / undo
    before: Mapped[dict | None] = mapped_column(JSON, default=None)
    after: Mapped[dict | None] = mapped_column(JSON, default=None)
    undone_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    undone_from_log_id: Mapped[int | None] = mapped_column(default=None)
