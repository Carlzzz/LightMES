from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem, FirstInspectionRecord,
    FirstInspectionCheckResult, FirstInspectionState,
    TestDataTemplate, TestDataField, TestDataRecord, TestDataValue,
    OperationRecord, SerialUnit, WorkOrder,
)
from lightmes.modules.production.schemas import (
    FirstInspectionConfigCreate, FirstInspectionConfigRead,
    FirstInspectionCheckItemCreate, FirstInspectionCheckItemRead,
    FirstInspectionRecordRead, FirstInspectionCheckResultRead,
    FirstInspectionStateRead, FirstInspectionSubmitInput, FirstInspectionReleaseInput,
    TestDataTemplateCreate, TestDataTemplateRead, TestDataFieldCreate, TestDataFieldRead,
    TestDataRecordRead, TestDataValueRead, TestDataRecordSubmitInput,
)
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.models import Operation, WorkStation


class FirstInspectionService:
    """首检服务"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_config(self, data: FirstInspectionConfigCreate) -> FirstInspectionConfig:
        """创建首检配置"""
        # 验证工序和作业站是否存在
        op = self.db.get(Operation, data.operation_id)
        if op is None:
            raise ValueError(f"工序不存在: {data.operation_id}")

        if data.work_station_id is not None:
            ws = self.db.get(WorkStation, data.work_station_id)
            if ws is None:
                raise ValueError(f"作业站不存在: {data.work_station_id}")

        config = FirstInspectionConfig(
            operation_id=data.operation_id,
            work_station_id=data.work_station_id,
            name=data.name,
            is_enabled=data.is_enabled,
            trigger_new_order=data.trigger_new_order,
            trigger_material_change=data.trigger_material_change,
            trigger_tooling_change=data.trigger_tooling_change,
            trigger_param_revision=data.trigger_param_revision,
            trigger_abnormal_restart=data.trigger_abnormal_restart,
            trigger_shift_handover=data.trigger_shift_handover,
            trigger_cold_start=data.trigger_cold_start,
            trigger_previous_failed=data.trigger_previous_failed,
            sample_size=data.sample_size,
            require_authorization=data.require_authorization,
            authorized_roles=data.authorized_roles,
            quarantine_on_fail=data.quarantine_on_fail,
        )
        self.db.add(config)
        self.db.flush()

        # 创建检查项
        for item_data in data.check_items:
            item = FirstInspectionCheckItem(
                config_id=config.id,
                seq=item_data.seq,
                name=item_data.name,
                description=item_data.description,
                check_type=item_data.check_type,
                unit=item_data.unit,
                standard_value=item_data.standard_value,
                min_value=item_data.min_value,
                max_value=item_data.max_value,
                is_mandatory=item_data.is_mandatory,
            )
            self.db.add(item)

        self.db.flush()
        return config

    def get_config(self, config_id: int) -> FirstInspectionConfig | None:
        return self.db.get(FirstInspectionConfig, config_id)

    def get_config_by_operation(self, operation_id: int, work_station_id: int | None = None) -> FirstInspectionConfig | None:
        """获取工序的首检配置，优先匹配指定作业站的配置"""
        query = select(FirstInspectionConfig).where(
            FirstInspectionConfig.operation_id == operation_id,
            FirstInspectionConfig.is_enabled == True,
        )

        # 优先查找匹配作业站的配置
        if work_station_id is not None:
            ws_config = self.db.execute(
                query.where(FirstInspectionConfig.work_station_id == work_station_id)
            ).scalar_one_or_none()
            if ws_config is not None:
                return ws_config

        # 查找通用配置（不限制作业站）
        return self.db.execute(
            query.where(FirstInspectionConfig.work_station_id.is_(None))
        ).scalar_one_or_none()

    def list_check_items(self, config_id: int) -> list[FirstInspectionCheckItem]:
        return list(self.db.execute(
            select(FirstInspectionCheckItem)
            .where(FirstInspectionCheckItem.config_id == config_id)
            .order_by(FirstInspectionCheckItem.seq)
        ).scalars().all())

    def check_needs_inspection(
        self, config: FirstInspectionConfig, work_order_id: int, operation_id: int,
    ) -> tuple[bool, str | None, FirstInspectionState | None]:
        """检查是否需要进行首检

        Returns:
            (是否需要首检, 触发原因, 当前状态)
        """
        state = self.db.execute(
            select(FirstInspectionState)
            .where(
                FirstInspectionState.work_order_id == work_order_id,
                FirstInspectionState.operation_id == operation_id,
            )
        ).scalar_one_or_none()

        if state is None:
            # 首次访问，创建状态记录
            state = FirstInspectionState(
                work_order_id=work_order_id,
                operation_id=operation_id,
            )
            self.db.add(state)
            self.db.flush()

        # 检查是否已有有效通过的首检
        if state.last_passed_at is not None:
            # 检查是否有新的触发条件满足
            trigger, reason = self._check_triggers(config, state)
            if trigger:
                return True, reason, state
            return False, None, state

        # 没有通过记录，需要首检
        return True, "new_order", state

    def _check_triggers(self, config: FirstInspectionConfig, state: FirstInspectionState) -> tuple[bool, str | None]:
        """检查各触发条件"""
        now = datetime.now()

        # 1. 新工单/从未通过
        if state.last_passed_at is None:
            return True, "new_order"

        # 2. 冷启动（停产≥4小时）
        if config.trigger_cold_start and state.last_produced_at is not None:
            if (now - state.last_produced_at) >= timedelta(hours=4):
                return True, "cold_start"

        # 3. 交接班（每天两次交班：8:00和20:00简化处理）
        if config.trigger_shift_handover:
            today_str = now.date().isoformat()
            if state.last_shift_date != today_str:
                # 简单判断：每天第一次生产触发
                return True, "shift_handover"

        # 4. 异常重启
        if config.trigger_abnormal_restart and state.is_abnormal_state:
            return True, "abnormal_restart"

        # 其他触发器需要更多上下文支持，暂简化
        # 物料变更、工装变更、参数修订需要额外的跟踪机制

        return False, None

    def create_inspection_record(
        self, config: FirstInspectionConfig, work_order_id: int, operation_id: int,
        work_station_id: int, inspector_id: int, trigger_reason: str,
        serial_unit_id: int | None = None, trigger_detail: str | None = None,
    ) -> FirstInspectionRecord:
        """创建首检记录"""
        record = FirstInspectionRecord(
            config_id=config.id,
            work_order_id=work_order_id,
            operation_id=operation_id,
            work_station_id=work_station_id,
            serial_unit_id=serial_unit_id,
            trigger_reason=trigger_reason,
            trigger_detail=trigger_detail,
            inspector_id=inspector_id,
            status="pending",
        )
        self.db.add(record)
        self.db.flush()
        return record

    def submit_inspection(
        self, data: FirstInspectionSubmitInput, inspector_id: int,
    ) -> FirstInspectionRecord:
        """提交首检结果"""
        record = self.db.get(FirstInspectionRecord, data.record_id)
        if record is None:
            raise ValueError(f"首检记录不存在: {data.record_id}")

        if record.status != "pending":
            raise ValueError(f"首检记录状态不是待提交: {record.status}")

        config = self.db.get(FirstInspectionConfig, record.config_id)
        check_items = {item.id: item for item in self.list_check_items(config.id)}

        all_pass = True

        # 保存检查结果
        for result_data in data.check_results:
            item = check_items.get(result_data.check_item_id)
            if item is None:
                raise ValueError(f"检查项不存在: {result_data.check_item_id}")

            is_pass = self._evaluate_check_result(item, result_data)
            if not is_pass and item.is_mandatory:
                all_pass = False

            result = FirstInspectionCheckResult(
                record_id=record.id,
                check_item_id=item.id,
                check_item_name=item.name,
                result_type=result_data.result_type,
                boolean_value=result_data.boolean_value,
                numeric_value=result_data.numeric_value,
                text_value=result_data.text_value,
                is_pass=is_pass,
                remark=result_data.remark,
            )
            self.db.add(result)

        # 更新记录状态
        record.status = "passed" if all_pass else "failed"
        record.inspector_id = inspector_id
        record.remark = data.remark

        # 更新状态跟踪
        state = self.db.execute(
            select(FirstInspectionState)
            .where(
                FirstInspectionState.work_order_id == record.work_order_id,
                FirstInspectionState.operation_id == record.operation_id,
            )
        ).scalar_one()

        state.last_inspection_record_id = record.id
        state.is_abnormal_state = False

        if record.status == "passed":
            state.last_passed_at = datetime.now()
            state.last_shift_date = datetime.now().date().isoformat()

        self.db.flush()
        return record

    def submit_new_inspection(
        self, config: FirstInspectionConfig, work_order_id: int, operation_id: int,
        work_station_id: int, inspector_id: int, trigger_reason: str,
        serial_unit_id: int | None,
        check_results: list,
        remark: str | None = None,
    ) -> FirstInspectionRecord:
        """创建 + 提交首检记录，返回带最终 status (passed/failed) 的 record。

        若 FirstInspectionState 不存在则创建（与 check_needs_inspection 一致），
        使本 helper 可独立调用而不依赖前置 check_needs_inspection。
        """
        state = self.get_state(work_order_id, operation_id)
        if state is None:
            state = FirstInspectionState(
                work_order_id=work_order_id, operation_id=operation_id)
            self.db.add(state)
            self.db.flush()
        record = self.create_inspection_record(
            config, work_order_id, operation_id, work_station_id,
            inspector_id, trigger_reason,
            serial_unit_id=serial_unit_id)
        return self.submit_inspection(
            FirstInspectionSubmitInput(
                record_id=record.id, check_results=check_results, remark=remark),
            inspector_id)

    def _evaluate_check_result(
        self, item: FirstInspectionCheckItem, result_data,
    ) -> bool:
        """评估检查项是否通过"""
        if item.check_type == "boolean":
            return bool(result_data.boolean_value)
        elif item.check_type == "numeric":
            val = result_data.numeric_value
            if val is None:
                return False
            ok = True
            if item.min_value is not None and val < item.min_value:
                ok = False
            if item.max_value is not None and val > item.max_value:
                ok = False
            return ok
        elif item.check_type == "text":
            # 文本类型只要有值就通过（或可扩展正则校验）
            return bool(result_data.text_value)
        return False

    def release_inspection(
        self, data: FirstInspectionReleaseInput, release_by_id: int,
    ) -> FirstInspectionRecord:
        """授权放行首检"""
        record = self.db.get(FirstInspectionRecord, data.record_id)
        if record is None:
            raise ValueError(f"首检记录不存在: {data.record_id}")

        if record.status not in ("failed", "passed"):
            raise ValueError(f"当前状态 {record.status} 不可放行")

        config = self.db.get(FirstInspectionConfig, record.config_id)

        if config.require_authorization:
            # 检查用户角色权限（role 是 FK 关系 role_obj，非 User 字段）
            user = self.db.get(User, release_by_id)
            if user is None:
                raise ValueError(f"用户不存在: {release_by_id}")
            role_name = user.role_obj.name if user.role_obj else None
            if config.authorized_roles and role_name not in config.authorized_roles:
                raise ValueError(f"用户角色 {role_name} 无权放行")

        record.released_by_id = release_by_id
        record.released_at = datetime.now()
        record.release_remark = data.release_remark

        # 即使失败也放行，需要记录
        if record.status == "failed":
            record.status = "waived"

        self.db.flush()
        return record

    def get_state(self, work_order_id: int, operation_id: int) -> FirstInspectionState | None:
        return self.db.execute(
            select(FirstInspectionState)
            .where(
                FirstInspectionState.work_order_id == work_order_id,
                FirstInspectionState.operation_id == operation_id,
            )
        ).scalar_one_or_none()


class TestDataService:
    """测试数据服务"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_template(self, data: TestDataTemplateCreate) -> TestDataTemplate:
        """创建测试数据模板"""
        op = self.db.get(Operation, data.operation_id)
        if op is None:
            raise ValueError(f"工序不存在: {data.operation_id}")

        if data.work_station_id is not None:
            ws = self.db.get(WorkStation, data.work_station_id)
            if ws is None:
                raise ValueError(f"作业站不存在: {data.work_station_id}")

        template = TestDataTemplate(
            operation_id=data.operation_id,
            work_station_id=data.work_station_id,
            name=data.name,
            description=data.description,
            is_enabled=data.is_enabled,
            version=data.version,
        )
        self.db.add(template)
        self.db.flush()

        # 创建字段
        for field_data in data.fields:
            field = TestDataField(
                template_id=template.id,
                seq=field_data.seq,
                code=field_data.code,
                name=field_data.name,
                field_type=field_data.field_type,
                unit=field_data.unit,
                is_required=field_data.is_required,
                standard_value=field_data.standard_value,
                min_value=field_data.min_value,
                max_value=field_data.max_value,
                options=field_data.options,
                display_group=field_data.display_group,
            )
            self.db.add(field)

        self.db.flush()
        return template

    def get_template(self, template_id: int) -> TestDataTemplate | None:
        return self.db.get(TestDataTemplate, template_id)

    def get_template_by_operation(self, operation_id: int, work_station_id: int | None = None) -> TestDataTemplate | None:
        """获取工序的测试模板"""
        query = select(TestDataTemplate).where(
            TestDataTemplate.operation_id == operation_id,
            TestDataTemplate.is_enabled == True,
        )

        if work_station_id is not None:
            ws_template = self.db.execute(
                query.where(TestDataTemplate.work_station_id == work_station_id)
            ).scalar_one_or_none()
            if ws_template is not None:
                return ws_template

        return self.db.execute(
            query.where(TestDataTemplate.work_station_id.is_(None))
        ).scalar_one_or_none()

    def list_fields(self, template_id: int) -> list[TestDataField]:
        return list(self.db.execute(
            select(TestDataField)
            .where(TestDataField.template_id == template_id)
            .order_by(TestDataField.seq)
        ).scalars().all())

    def submit_test_data(
        self, data: TestDataRecordSubmitInput, operator_id: int,
    ) -> TestDataRecord:
        """提交测试数据"""
        op_record = self.db.get(OperationRecord, data.operation_record_id)
        if op_record is None:
            raise ValueError(f"工序记录不存在: {data.operation_record_id}")

        template = self.get_template_by_operation(op_record.operation_id, op_record.work_station_id)
        if template is None:
            raise ValueError("该工序未配置测试数据模板")

        fields = {f.id: f for f in self.list_fields(template.id)}

        # 创建测试记录
        record = TestDataRecord(
            template_id=template.id,
            operation_record_id=op_record.id,
            serial_unit_id=op_record.serial_unit_id,
            work_order_id=op_record.work_order_id,
            operation_id=op_record.operation_id,
            work_station_id=op_record.work_station_id,
            operator_id=operator_id,
            overall_result="pending",
            test_started_at=datetime.now(),
            remark=data.remark,
        )
        self.db.add(record)
        self.db.flush()

        all_pass = True

        # 保存测试值
        for value_data in data.values:
            field = fields.get(value_data.field_id)
            if field is None:
                raise ValueError(f"字段不存在: {value_data.field_id}")

            is_pass, out_of_spec = self._evaluate_field_value(field, value_data)
            if field.is_required and is_pass is False:
                all_pass = False

            test_value = TestDataValue(
                record_id=record.id,
                field_id=field.id,
                field_code=field.code,
                field_name=field.name,
                value_type=value_data.value_type,
                numeric_value=value_data.numeric_value,
                boolean_value=value_data.boolean_value,
                text_value=value_data.text_value,
                is_pass=is_pass,
                out_of_spec=out_of_spec,
            )
            self.db.add(test_value)

        record.overall_result = "passed" if all_pass else "failed"
        record.test_completed_at = datetime.now()

        self.db.flush()
        return record

    def _evaluate_field_value(self, field: TestDataField, value_data) -> tuple[bool | None, bool]:
        """评估字段值是否合格

        Returns:
            (是否合格, 是否超公差)
        """
        if field.field_type == "numeric":
            val = value_data.numeric_value
            if val is None:
                return None, False

            out_of_spec = False
            if field.min_value is not None and val < field.min_value:
                out_of_spec = True
            if field.max_value is not None and val > field.max_value:
                out_of_spec = True

            return not out_of_spec, out_of_spec

        elif field.field_type == "boolean":
            val = value_data.boolean_value
            if val is None:
                return None, False
            # Boolean类型需要根据standard_value判断
            if field.standard_value is not None:
                expected = field.standard_value.lower() in ("true", "1", "yes", "ok")
                return val == expected, val != expected

            return bool(val), not bool(val)

        return True, False

    def get_record(self, record_id: int) -> TestDataRecord | None:
        return self.db.get(TestDataRecord, record_id)

    def get_values(self, record_id: int) -> list[TestDataValue]:
        return list(self.db.execute(
            select(TestDataValue).where(TestDataValue.record_id == record_id)
        ).scalars().all())
