from datetime import datetime, timezone
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
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

    def bind_components(
        self, parent_su, components: list[ComponentBind],
        operator_id: int | None, station_pass_id: int | None = None,
        operation_record_id: int | None = None,
    ) -> list[GenealogyBind]:
        items = self.query.get_active_bom_items(parent_su.product_id)
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
            if track == "serial":
                if not comp.component_sn:
                    raise ValidationError("唯一件组件必须提供 component_sn")
                occupied = self.binds.list_active_by_component_sn(comp.component_sn)
                if occupied:
                    raise ConflictError(
                        f"该唯一件已装配在其他成品上: {comp.component_sn}")
            elif track == "batch":
                if not comp.component_batch_no:
                    raise ValidationError("批次件组件必须提供 component_batch_no")
            bind = self.binds.add(GenealogyBind(
                parent_sn_id=parent_su.id,
                component_product_id=comp.component_product_id,
                component_type=track,
                component_sn=comp.component_sn,
                component_batch_no=comp.component_batch_no,
                qty=comp.qty,
                operator_id=operator_id,
                station_pass_id=station_pass_id,
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
        self.db.flush()
        event_bus.publish(GenealogyUnbound(
            bind_id=bind.id, parent_sn_id=bind.parent_sn_id, reason=reason,
        ))
        return bind
