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
    OperationPassInput, OperationPassResult, OpInfo,
)
from lightmes.modules.production.events import OperationPassed, SerialUnitFinished
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
            if su.status in ("finished", "scrapped"):
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

        # 5. 三层防跳站：作业站须 = 期望工序默认作业站，且该作业站属工单产线
        ws = self.query.get_work_station(data.work_station_id)
        if ws is None:
            raise NotFoundError(f"作业站不存在: {data.work_station_id}")
        if ws.line_id != wo.line_id:
            raise BusinessRuleError("当前作业站不属于本工单产线")
        if data.work_station_id != expected.default_work_station_id:
            raise BusinessRuleError(
                f"应到工序 {expected.seq} {expected.name} 对应作业站，当前作业站不符")

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
        next_info = (OpInfo(seq=remaining[0].seq, name=remaining[0].name,
                            work_station_id=remaining[0].default_work_station_id)
                     if remaining else None)
        return OperationPassResult(
            sn=su.sn,
            passed_op=OpInfo(seq=expected.seq, name=expected.name,
                             work_station_id=expected.default_work_station_id),
            next_op=next_info, is_finished=su.status == "finished",
            work_order_status=wo.status, bound_count=bound_count,
            param_count=param_count,
        )
