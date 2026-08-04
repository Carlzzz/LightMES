from sqlalchemy.orm import Session

from lightmes.modules.production.repository import (
    SerialUnitRepository, StationPassRepository,
)
from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.modules.trace.schemas import (
    BindView, PassView, GenealogyView, HistoryView, ParentRef,
)
from lightmes.shared.errors import NotFoundError, ValidationError


def _bind_view(b: GenealogyBind) -> BindView:
    return BindView(
        component_product_id=b.component_product_id,
        component_type=b.component_type,
        component_ref=b.component_sn or b.component_batch_no or "",
        qty=float(b.qty),
        status=b.status,
    )


class TraceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.binds = GenealogyBindRepository(db)
        self.serial_units = SerialUnitRepository(db)
        self.passes = StationPassRepository(db)

    def genealogy_of(self, sn: str, include_unbound: bool = False) -> GenealogyView:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        binds = (self.binds.list_by_parent(su.id) if include_unbound
                 else self.binds.list_active_by_parent(su.id))
        return GenealogyView(sn=sn, components=[_bind_view(b) for b in binds])

    def where_used(
        self, component_sn: str | None = None, component_batch_no: str | None = None,
    ) -> list[ParentRef]:
        if not component_sn and not component_batch_no:
            raise ValidationError("需提供 component_sn 或 component_batch_no")
        if component_sn:
            binds = self.binds.list_by_component_sn(component_sn)
        else:
            binds = self.binds.list_by_component_batch(component_batch_no)
        return [
            ParentRef(
                parent_sn_id=b.parent_sn_id,
                component_ref=b.component_sn or b.component_batch_no or "",
                status=b.status,
            )
            for b in binds
        ]

    def history_of(self, sn: str) -> HistoryView:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        passes = self.passes.list_by_serial_unit(su.id)
        binds = self.binds.list_by_parent(su.id)
        return HistoryView(
            sn=sn,
            passes=[PassView(
                routing_step_id=p.routing_step_id, station_id=p.station_id,
                result=p.result, pass_time=p.pass_time,
            ) for p in passes],
            components=[_bind_view(b) for b in binds],
        )
