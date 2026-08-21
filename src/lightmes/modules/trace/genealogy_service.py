from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import BatchMaterialConsumption, WorkOrder
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.repository import MaterialLotRepository
from lightmes.modules.production.process_snapshot import (
    has_snapshot,
    snapshot_bom_items,
)
from lightmes.modules.trace.events import GenealogyBound, GenealogyUnbound
from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import (
    NotFoundError, BusinessRuleError, ValidationError, ConflictError,
)
from lightmes.shared.events import event_bus


class GenealogyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.binds = GenealogyBindRepository(db)

    def _bom_items_for_unit(self, parent_su) -> list:
        """Prefer the BOM frozen at work-order release time."""
        if parent_su.work_order_id is not None:
            work_order = self.db.get(WorkOrder, parent_su.work_order_id)
            if work_order is not None and has_snapshot(work_order):
                return snapshot_bom_items(work_order)
        return self.query.get_active_bom_items(parent_su.product_id)

    def return_batch_consumption(self, *, material_lot_id: int, quantity: float, reason: str) -> None:
        MaterialLotService(self.db).return_consumed(
            material_lot_id=material_lot_id,
            quantity=quantity,
            reason=reason,
        )

    def bind_components(
        self, parent_su, components: list[ComponentBind],
        operator_id: int | None,
        operation_record_id: int | None = None,
        current_op_seq: int | None = None,
    ) -> list[GenealogyBind]:
        items = self._bom_items_for_unit(parent_su)
        if not items:
            raise BusinessRuleError("成品无 active BOM，无法绑定组件")
        bom_by_component = {i.component_product_id: i for i in items}
        result: list[GenealogyBind] = []
        for comp in components:
            item = bom_by_component.get(comp.component_product_id)
            if item is None:
                raise BusinessRuleError(
                    f"组件不属于本产品 BOM: {comp.component_product_id}")
            track = item.track_mode
            expected_p = self.query.get_product(item.component_product_id)
            expected_code = expected_p.code if expected_p else f"#{item.component_product_id}"
            if track == "serial":
                if not comp.component_sn:
                    raise ValidationError("唯一件组件必须提供 component_sn")
                # 料号校验：扫码的 SN 必须真实存在且属于 BOM 声明的料号
                from lightmes.modules.production.repository import SerialUnitRepository
                comp_su = SerialUnitRepository(self.db).get_by_sn(comp.component_sn)
                if comp_su is None:
                    raise NotFoundError(f"唯一件 SN 不存在: {comp.component_sn}")
                if comp_su.product_id != item.component_product_id:
                    scanned_p = self.query.get_product(comp_su.product_id)
                    scanned_code = scanned_p.code if scanned_p else f"#{comp_su.product_id}"
                    raise BusinessRuleError(
                        f"料号不匹配：SN {comp.component_sn} 属于 [{scanned_code}]，"
                        f"本工位应装 [{expected_code}]")
                occupied = self.binds.list_active_by_component_sn(comp.component_sn)
                if occupied:
                    raise ConflictError(
                        f"该唯一件已装配在其他成品上: {comp.component_sn}")
            elif track == "batch":
                if not comp.component_batch_no:
                    raise ValidationError("批次件组件必须提供 component_batch_no")
                # 料号校验：扫码批次必须存在且属于 BOM 声明的料号
                lot = MaterialLotRepository(self.db).get_by_code(comp.component_batch_no)
                if lot is None:
                    raise NotFoundError(f"物料批次不存在: {comp.component_batch_no}")
                if lot.product_id != item.component_product_id:
                    scanned_p = self.query.get_product(lot.product_id)
                    scanned_code = scanned_p.code if scanned_p else f"#{lot.product_id}"
                    raise BusinessRuleError(
                        f"料号不匹配：批次 {comp.component_batch_no} 是 [{scanned_code}]，"
                        f"本工位应装 [{expected_code}]")
            # 扫错件拦截（current_op_seq 非 None 且 BOM 声明了 consume_at_operation_seq 时校验）
            if (current_op_seq is not None
                    and item.consume_at_operation_seq is not None
                    and item.consume_at_operation_seq != current_op_seq):
                raise BusinessRuleError(
                    f"此物料应在工序 {item.consume_at_operation_seq} 装配，"
                    f"不可在工序 {current_op_seq} 扫描")
            bind = self.binds.add(GenealogyBind(
                parent_sn_id=parent_su.id,
                component_product_id=comp.component_product_id,
                component_type=track,
                component_sn=comp.component_sn,
                component_batch_no=comp.component_batch_no,
                qty=comp.qty,
                operator_id=operator_id,
                operation_record_id=operation_record_id,
                status="active",
            ))
            event_bus.publish(GenealogyBound(
                parent_sn_id=parent_su.id,
                component_product_id=comp.component_product_id,
                component_type=track,
                component_ref=comp.component_sn or comp.component_batch_no or "",
            ))
            result.append(bind)
        return result

    def unbind(
        self, bind_id: int, reason: str | None, operator_id: int | None,
    ) -> GenealogyBind:
        bind = self.binds.get(bind_id)
        if bind is None:
            raise NotFoundError(f"谱系绑定不存在: {bind_id}")
        if bind.status != "active":
            raise BusinessRuleError(f"绑定非 active，不可解绑: {bind_id}")
        bind.status = "unbound"
        bind.unbind_time = datetime.now(timezone.utc)
        bind.unbind_reason = reason
        if bind.component_type == "batch" and bind.component_batch_no:
            lot = MaterialLotRepository(self.db).get_by_code(bind.component_batch_no)
            if lot is None:
                raise NotFoundError(f"物料批次不存在: {bind.component_batch_no}")
            consumed = self.db.execute(
                select(func.coalesce(func.sum(BatchMaterialConsumption.quantity), 0))
                .where(BatchMaterialConsumption.material_lot_id == lot.id)
            ).scalar_one()
            if float(consumed) > 0:
                self.return_batch_consumption(
                    material_lot_id=lot.id,
                    quantity=float(bind.qty or 0),
                    reason=reason or "",
                )
        self.db.flush()
        event_bus.publish(GenealogyUnbound(
            bind_id=bind.id, parent_sn_id=bind.parent_sn_id, reason=reason,
        ))
        return bind
