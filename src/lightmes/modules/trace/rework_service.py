from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.production.models import SerialUnit, WorkOrder
from lightmes.modules.production.batch_service import BatchService
from lightmes.modules.production.process_snapshot import get_work_order_process
from lightmes.modules.production.repository import SerialUnitRepository, CarrierBindingRepository
from lightmes.modules.trace.events import SerialUnitReworkStarted
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.shared.errors import (
    NotFoundError, BusinessRuleError, ValidationError, ConflictError,
)
from lightmes.shared.events import event_bus


class ReworkService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.genealogy = GenealogyService(db)
        self.carrier_bindings = CarrierBindingRepository(db)

    def rework(
        self, sn: str, target_seq: int,
        expected_repass_station_id: int,
        unbind_bind_ids: list[int] | None = None,
        reason: str | None = None, operator_id: int | None = None,
    ) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "scrapped":
            raise BusinessRuleError(f"SN 已判废，不可返工: {sn}")
        # 放宽：原 `>=` 改 `>`；reworking 态允许 ==（重选站位），非 reworking 态仍拒绝 ==
        if target_seq < 0 or target_seq > su.current_operation_seq:
            raise ValidationError(
                f"返工目标工序 {target_seq} 必须小于等于当前 {su.current_operation_seq}")
        if target_seq == su.current_operation_seq and su.status != "reworking":
            raise ValidationError(
                f"返工目标工序 {target_seq} 等于当前 {su.current_operation_seq}，"
                f"仅返工态可重选站位")
        # 校验 expected 站 ∈ 首个 re-pass 工序 allowed
        wo = self.db.get(WorkOrder, su.work_order_id)
        if wo is None:
            raise NotFoundError(f"工单不存在: {su.work_order_id}")
        operations = get_work_order_process(self.db, wo).operations
        first_repass_op = next((o for o in operations if o.seq > target_seq), None)
        if first_repass_op is None:
            raise ValidationError(f"target_seq {target_seq} 之后无工序可重做")
        allowed_ids = (
            first_repass_op.allowed_work_station_ids
            or [first_repass_op.default_work_station_id]
        )
        if expected_repass_station_id not in allowed_ids:
            raise ValidationError(
                f"站位 #{expected_repass_station_id} 不在工序 "
                f"{first_repass_op.seq} {first_repass_op.name} 的允许集合内")
        # 解绑组件
        for bind_id in (unbind_bind_ids or []):
            bind = self.genealogy.binds.get(bind_id)
            if bind is None or bind.parent_sn_id != su.id:
                raise NotFoundError(f"谱系绑定不存在或不属于本 SN: {bind_id}")
            self.genealogy.unbind(bind_id, reason=reason, operator_id=operator_id)
        prev_version = su.version
        result = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(status="reworking", current_operation_seq=target_seq,
                    rework_target_station_id=expected_repass_station_id,
                    version=prev_version + 1)
        )
        if result.rowcount == 0:
            raise ConflictError("该产品正被其他操作处理，请重试")
        self.db.refresh(su)
        event_bus.publish(SerialUnitReworkStarted(
            serial_unit_id=su.id, sn=su.sn, target_seq=target_seq,
        ))
        return su

    def scrap(self, sn: str, reason: str | None = None) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status not in ("in_process", "reworking", "quarantined", "finished"):
            raise BusinessRuleError(f"仅在制/返工/隔离/完工件可判废，当前: {su.status}")
        was_counted = su.is_counted
        # 清除载体码绑定（与完工路径一致）
        if su.carrier_code is not None:
            binding = self.carrier_bindings.active_by_serial_unit(su.id)
            if binding is not None:
                binding.unbound_at = datetime.now()
                binding.unbound_reason = "scrap"
            su.carrier_code = None
        su.status = "scrapped"
        # 完工件先计入 produced；报废时必须从 produced 换到 scrap，
        # 避免同一物理件同时占用两个工单口径。
        if su.work_order_id is not None:
            wo = self.db.get(WorkOrder, su.work_order_id)
            if wo is not None:
                counter_values = {"scrap_qty": WorkOrder.scrap_qty + 1}
                if was_counted:
                    counter_values["produced_qty"] = WorkOrder.produced_qty - 1
                new_produced, new_scrap = self.db.execute(
                    update(WorkOrder)
                    .where(WorkOrder.id == wo.id)
                    .values(**counter_values)
                    .returning(WorkOrder.produced_qty, WorkOrder.scrap_qty)
                ).one()
                if was_counted:
                    BatchService(self.db).record_scrapped_finished_unit(su.batch_id)
                if wo.status in ("released", "in_process") and new_produced + new_scrap >= wo.qty:
                    self.db.execute(
                        update(WorkOrder).where(WorkOrder.id == wo.id)
                        .values(status="completed"))
                self.db.refresh(wo)
        self.db.flush()
        return su
