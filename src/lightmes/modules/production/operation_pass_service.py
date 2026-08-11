from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.production.models import (
    SerialUnit, OperationRecord, OperationParam, WorkOrder,
)
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository, OperationParamRepository,
    CarrierBindingRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import (
    OperationPassInput, OperationPassResult, OperationSkipInput, OperationSkipResult, OpInfo,
)
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.production.events import OperationPassed, OperationSkipped, SerialUnitFinished
from lightmes.modules.production.quality_service import FirstInspectionService
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import NotFoundError, BusinessRuleError, ConflictError, SkillError
from lightmes.shared.events import event_bus


class OperationPassService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.serial_units = SerialUnitRepository(db)
        self.records = OperationRecordRepository(db)
        self.params = OperationParamRepository(db)
        self.work_orders = WorkOrderRepository(db)

    def pass_operation(self, data: OperationPassInput) -> OperationPassResult:
        # 1+3. 定位单元：SN → 载体码 → 工单号(取第一个 pending)
        su = None
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                su = self.serial_units.get_active_by_carrier(data.sn)
            if su is None:
                raise NotFoundError(f"未找到 SN 或载体码: {data.sn}")
            if su.status in ("finished", "scrapped", "quarantined"):
                raise BusinessRuleError(f"SN 已{su.status}，不可过站: {su.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首件过站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = self.serial_units.first_pending_by_work_order(wo.id)
            if su is None:
                raise BusinessRuleError("工单 SN 已全部投产")

        # 2. 工单状态
        if wo is None:
            raise NotFoundError("工单不存在")
        if wo.status not in ("released", "in_process"):
            raise BusinessRuleError(f"工单状态不允许过站: {wo.status}")

        operations = self.query.get_operations(wo.routing_id)
        if not operations:
            raise BusinessRuleError("工艺路径无工序")

        # 4. 期望下一工序（前向唯一→天然防重复）
        next_ops = [o for o in operations if o.seq > su.current_operation_seq]
        if not next_ops:
            raise BusinessRuleError("已完工，无后续工序")
        expected = next_ops[0]

        # 5. 三层防跳站：作业站须属工单产线；该工序的允许作业站须含当前作业站
        ws = self.query.get_work_station(data.work_station_id)
        if ws is None:
            raise NotFoundError(f"作业站不存在: {data.work_station_id}")
        if ws.line_id != wo.line_id:
            raise BusinessRuleError("当前作业站不属于本工单产线")
        allowed = self.query.get_allowed_work_stations(expected.id)
        allowed_ids = [w.id for w in allowed]
        if not allowed_ids:
            allowed_ids = [expected.default_work_station_id]  # 兜底（关联表空时退化为旧行为）
        if data.work_station_id not in allowed_ids:
            names = "、".join(w.name for w in allowed) or f"作业站 #{expected.default_work_station_id}"
            raise BusinessRuleError(
                f"该 SN 当前工序 {expected.seq} {expected.name} "
                f"应在【{names}】之一作业站做，当前作业站不符")

        # 5a. 返工首次 re-pass 站位硬卡（仅 reworking 态 + 已设预期站位时生效）
        if su.status == "reworking" and su.rework_target_station_id is not None:
            if data.work_station_id != su.rework_target_station_id:
                expected_ws = self.query.get_work_station(su.rework_target_station_id)
                current_ws = ws  # 步骤 5 已查
                raise BusinessRuleError(
                    f"该返工件须在【{expected_ws.name if expected_ws else f'#{su.rework_target_station_id}'}】重做，"
                    f"当前作业站【{current_ws.name if current_ws else f'#{data.work_station_id}'}】不符。"
                    f"如需更改，请重新发起返工选择正确站位。")

        # 5b. 技能校验（硬拦截）：工序有技能要求时，操作员该技能等级须 >= 要求
        if expected.required_skill_id is not None:
            level = (SkillService(self.db).get_operator_level(
                data.operator_id, expected.required_skill_id)
                if data.operator_id else None)
            if level is None or level < (expected.required_level or 0):
                raise SkillError(
                    f"操作员技能不足：工序 {expected.seq} {expected.name} "
                    f"需要技能等级 L{expected.required_level}+，当前 "
                    f"{level if level is not None else '无'}")

        # 5c. 首检硬卡：工序有启用的首检配置 + 触发条件命中时，必须提交合格的首检才能过站
        fi_svc = FirstInspectionService(self.db)
        fi_config = fi_svc.get_config_by_operation(expected.id, data.work_station_id)
        if fi_config and fi_config.is_enabled:
            needs, reason, _fi_state = fi_svc.check_needs_inspection(
                fi_config, wo.id, expected.id)
            if needs:
                if data.first_inspection is None or not data.first_inspection.check_results:
                    raise BusinessRuleError(
                        f"该工序需首检（触发：{reason}），请填写首检结果后过站")
                fi_record = fi_svc.submit_new_inspection(
                    config=fi_config, work_order_id=wo.id, operation_id=expected.id,
                    work_station_id=data.work_station_id, inspector_id=data.operator_id,
                    trigger_reason=reason, serial_unit_id=su.id,
                    check_results=data.first_inspection.check_results,
                    remark=data.first_inspection.remark)
                if fi_record.status == "failed":
                    defect = DefectService(self.db).log_defect_from_inspection(
                        fi_record=fi_record, sn=su.sn,
                        discovered_by=data.operator_id,
                        remark=f"首检不合格（触发：{reason}）")
                    self.db.commit()  # 保留 fi_record + defect + quarantined SN
                    raise BusinessRuleError(
                        f"首检不合格，SN 已隔离，缺陷记录 #{defect.id}。"
                        f"请前往 /quality/defects/{defect.id} 处理。")

        # 5d. 物料绑定必扫校验（仅最终工序检查累积绑定）：仅在最终工序强制校验
        #      检查累积已绑（之前工序扫的）+ 本次扫的 = BOM 需求
        bom_items = self.query.get_active_bom_items(wo.product_id)
        is_last_op = False
        if bom_items:
            is_last_op = (expected.id == operations[-1].id) if operations else False
            if is_last_op:
                from collections import Counter
                from lightmes.modules.trace.repository import GenealogyBindRepository
                # 累积已绑组件
                existing_binds = GenealogyBindRepository(self.db).list_active_by_parent(su.id)
                provided_counts: Counter[int] = Counter()
                for b in existing_binds:
                    provided_counts[b.component_product_id] += 1
                for c in data.components:
                    provided_counts[c.component_product_id] += 1
                missing = []
                for item in bom_items:
                    if item.track_mode == "none":
                        continue
                    comp = self.query.get_product(item.component_product_id)
                    comp_name = comp.name if comp else f"#{item.component_product_id}"
                    provided = provided_counts.get(item.component_product_id, 0)
                    required = int(item.qty) if item.track_mode == "serial" else 1
                    if provided == 0:
                        missing.append(f"{comp_name}（{item.track_mode}）")
                    elif item.track_mode == "serial" and provided < required:
                        missing.append(
                            f"{comp_name}（serial，需 {required} 件，已绑 {provided} 件）")
                if missing:
                    raise BusinessRuleError(
                        f"物料绑定不完整，不可过站：{', '.join(missing)}")

        # 6. 写工序记录 + 乐观锁更新 serial_unit
        record = self.records.add(OperationRecord(
            serial_unit_id=su.id, work_order_id=wo.id, operation_id=expected.id,
            work_station_id=data.work_station_id, line_id=wo.line_id,
            operator_id=data.operator_id, result="pass",
        ))
        prev_version = su.version
        r = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(current_operation_seq=expected.seq, version=prev_version + 1)
        )
        if r.rowcount == 0:
            raise ConflictError("该产品正被其他作业站处理，请重试")
        self.db.refresh(su)

        # 6a. 首次 re-pass 成功后清除返工站位约束
        if su.status == "reworking" and su.rework_target_station_id is not None:
            su.rework_target_station_id = None

        # 7. 绑料（同事务，失败整单回滚）
        bound_count = 0
        if data.components:
            try:
                binds = GenealogyService(self.db).bind_components(
                    su,
                    [ComponentBind(
                        component_product_id=c.component_product_id,
                        component_sn=c.component_sn,
                        component_batch_no=c.component_batch_no,
                        qty=c.qty,
                    ) for c in data.components],
                    operator_id=data.operator_id,
                    operation_record_id=record.id,
                    current_op_seq=expected.seq,
                )
            except Exception:
                self.db.rollback()
                raise
            bound_count = len(binds)

        # 8. 参数录入
        param_count = 0
        for pm in data.params:
            self.params.add(OperationParam(
                operation_record_id=record.id, param_key=pm.param_key,
                param_value=pm.param_value, unit=pm.unit, source="manual",
            ))
            param_count += 1

        # 9. 末工序完工（防重复计数沿用 is_counted）
        is_last = expected.seq == operations[-1].seq
        if is_last:
            su.status = "finished"
            # 完工自动解绑：清载体码 + 写 binding.unbound_at/unbound_reason=finish，
            # 让托盘立即复用并留审计（否则 finished 单元仍占 carrier_code，
            # 部分唯一索引会拒绝复用并抛 500）
            if su.carrier_code is not None:
                binding = CarrierBindingRepository(self.db).active_by_serial_unit(su.id)
                if binding is not None:
                    binding.unbound_at = datetime.now()
                    binding.unbound_reason = "finish"
                su.carrier_code = None
            if not su.is_counted:
                su.is_counted = True
                event_bus.publish(SerialUnitFinished(
                    serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id))
                new_qty = self.db.execute(
                    update(WorkOrder).where(WorkOrder.id == wo.id)
                    .values(produced_qty=WorkOrder.produced_qty + 1)
                    .returning(WorkOrder.produced_qty)
                ).scalar_one()
                if new_qty >= wo.qty:
                    self.db.execute(update(WorkOrder).where(WorkOrder.id == wo.id)
                                    .values(status="completed"))
                self.db.refresh(wo)

        # 10. 工单/返工件状态复位
        if wo.status == "released":
            wo.status = "in_process"
        if su.status in ("reworking", "pending"):
            su.status = "in_process"
        self.db.flush()

        # 11. 事件
        event_bus.publish(OperationPassed(
            serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            operation_id=expected.id, work_station_id=data.work_station_id,
            line_id=wo.line_id))

        remaining = [o for o in operations if o.seq > expected.seq]
        next_info = None
        next_op_can_continue_here = False
        if remaining:
            next_op_obj = remaining[0]
            next_info = OpInfo(seq=next_op_obj.seq, name=next_op_obj.name,
                               work_station_id=next_op_obj.default_work_station_id)
            next_allowed = self.query.get_allowed_work_stations(next_op_obj.id)
            next_allowed_ids = [w.id for w in next_allowed] or [next_op_obj.default_work_station_id]
            next_op_can_continue_here = data.work_station_id in next_allowed_ids
        return OperationPassResult(
            sn=su.sn,
            passed_op=OpInfo(seq=expected.seq, name=expected.name,
                             work_station_id=expected.default_work_station_id),
            next_op=next_info, is_finished=su.status == "finished",
            work_order_status=wo.status, bound_count=bound_count,
            param_count=param_count,
            next_op_can_continue_here=next_op_can_continue_here,
        )

    def skip_operation(self, data: OperationSkipInput) -> OperationSkipResult:
        """跳过当前工序：写 result='skip' 记录，推进 seq，不绑料/不校验技能/不完工。"""
        # 跳站是特权操作，operator_id 必须有值留审计（路由层 supervisor 守卫已确保登录）
        if data.operator_id is None:
            raise BusinessRuleError("跳站需登录操作员，operator_id 不可为空")
        # 1+3. 定位单元：SN -> 载体码 -> 工单号(取第一个 pending)
        su = None
        if data.sn is not None:
            su = self.serial_units.get_by_sn(data.sn)
            if su is None:
                su = self.serial_units.get_active_by_carrier(data.sn)
            if su is None:
                raise NotFoundError(f"未找到 SN 或载体码: {data.sn}")
            if su.status in ("finished", "scrapped", "quarantined"):
                raise BusinessRuleError(f"SN 已{su.status}，不可跳站: {su.sn}")
            wo = self.work_orders.get(su.work_order_id)
        else:
            if data.work_order_code is None:
                raise BusinessRuleError("首件跳站需提供工单号")
            wo = self.work_orders.get_by_code(data.work_order_code)
            if wo is None:
                raise NotFoundError(f"工单不存在: {data.work_order_code}")
            su = self.serial_units.first_pending_by_work_order(wo.id)
            if su is None:
                raise BusinessRuleError("工单 SN 已全部投产")

        # 2. 工单状态
        if wo.status not in ("released", "in_process"):
            raise BusinessRuleError(f"工单状态不允许跳站: {wo.status}")

        operations = self.query.get_operations(wo.routing_id)
        if not operations:
            raise BusinessRuleError("工艺路径无工序")

        # 4. 期望下一工序
        next_ops = [o for o in operations if o.seq > su.current_operation_seq]
        if not next_ops:
            raise BusinessRuleError("已完工，无后续工序")
        expected = next_ops[0]

        # 末工序不可跳
        if expected.id == operations[-1].id:
            raise BusinessRuleError("末工序不可跳过")

        # 5. 三层防跳站
        ws = self.query.get_work_station(data.work_station_id)
        if ws is None:
            raise NotFoundError(f"作业站不存在: {data.work_station_id}")
        if ws.line_id != wo.line_id:
            raise BusinessRuleError("当前作业站不属于本工单产线")
        allowed = self.query.get_allowed_work_stations(expected.id)
        allowed_ids = [w.id for w in allowed] or [expected.default_work_station_id]
        if data.work_station_id not in allowed_ids:
            names = "、".join(w.name for w in allowed) or f"作业站 #{expected.default_work_station_id}"
            raise BusinessRuleError(
                f"该 SN 当前工序 {expected.seq} {expected.name} "
                f"应在【{names}】之一作业站做，当前作业站不符")

        # 6. 写工序记录 + 乐观锁推进 seq（跳过技能/BOM/绑定/参数/完工）
        record = self.records.add(OperationRecord(
            serial_unit_id=su.id, work_order_id=wo.id, operation_id=expected.id,
            work_station_id=data.work_station_id, line_id=wo.line_id,
            operator_id=data.operator_id, result="skip", remark=data.reason,
        ))
        prev_version = su.version
        r = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(current_operation_seq=expected.seq, version=prev_version + 1)
        )
        if r.rowcount == 0:
            raise ConflictError("该产品正被其他作业站处理，请重试")
        self.db.refresh(su)

        # 10. 工单/返工件状态复位（skip 不完工）
        if wo.status == "released":
            wo.status = "in_process"
        if su.status in ("reworking", "pending"):
            su.status = "in_process"
        self.db.flush()

        # 11. 事件
        event_bus.publish(OperationSkipped(
            serial_unit_id=su.id, sn=su.sn, work_order_id=wo.id,
            operation_id=expected.id, work_station_id=data.work_station_id,
            line_id=wo.line_id, reason=data.reason))

        remaining = [o for o in operations if o.seq > expected.seq]
        next_info = None
        next_op_can_continue_here = False
        if remaining:
            next_op_obj = remaining[0]
            next_info = OpInfo(seq=next_op_obj.seq, name=next_op_obj.name,
                               work_station_id=next_op_obj.default_work_station_id)
            next_allowed = self.query.get_allowed_work_stations(next_op_obj.id)
            next_allowed_ids = [w.id for w in next_allowed] or [next_op_obj.default_work_station_id]
            next_op_can_continue_here = data.work_station_id in next_allowed_ids
        return OperationSkipResult(
            sn=su.sn,
            skipped_op=OpInfo(seq=expected.seq, name=expected.name,
                              work_station_id=expected.default_work_station_id),
            next_op=next_info, is_finished=False,
            work_order_status=wo.status,
            next_op_can_continue_here=next_op_can_continue_here,
        )
