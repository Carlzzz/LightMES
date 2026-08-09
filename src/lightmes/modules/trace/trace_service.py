from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository, OperationParamRepository,
)
from lightmes.modules.trace.models import GenealogyBind
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.modules.trace.schemas import (
    BindView, OpRecordView, ParamView, GenealogyView, HistoryView, ParentRef,
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
        self.query = MasterDataQueryService(db)
        self.binds = GenealogyBindRepository(db)
        self.serial_units = SerialUnitRepository(db)
        self.records = OperationRecordRepository(db)
        self.params = OperationParamRepository(db)

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
        result = []
        for b in binds:
            parent_su = self.serial_units.get(b.parent_sn_id)
            result.append(ParentRef(
                parent_sn_id=b.parent_sn_id,
                parent_sn=parent_su.sn if parent_su else str(b.parent_sn_id),
                component_ref=b.component_sn or b.component_batch_no or "",
                status=b.status,
            ))
        return result

    def history_of(self, sn: str) -> HistoryView:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        records = self.records.list_by_serial_unit(su.id)
        binds = self.binds.list_by_parent(su.id)
        params = self.params.list_by_serial_unit(su.id)
        # Enrich records with operation + station names
        from lightmes.modules.masterdata.models import Operation
        op_cache: dict[int, tuple[str, int]] = {}
        ws_cache: dict[int, str] = {}
        rec_views = []
        for r in records:
            if r.operation_id not in op_cache:
                op = self.db.get(Operation, r.operation_id)
                op_cache[r.operation_id] = (
                    (op.name if op else f"#{r.operation_id}"),
                    (op.seq if op else 0)
                )
            if r.work_station_id not in ws_cache:
                ws = self.query.get_work_station(r.work_station_id)
                ws_cache[r.work_station_id] = ws.name if ws else f"#{r.work_station_id}"
            op_name, op_seq = op_cache[r.operation_id]
            rec_views.append(OpRecordView(
                operation_id=r.operation_id,
                operation_name=op_name,
                operation_seq=op_seq,
                work_station_id=r.work_station_id,
                work_station_name=ws_cache[r.work_station_id],
                line_id=r.line_id,
                result=r.result,
                end_time=r.end_time,
            ))
        return HistoryView(
            sn=sn,
            records=rec_views,
            components=[_bind_view(b) for b in binds],
            params=[ParamView(
                param_key=p.param_key, param_value=p.param_value, unit=p.unit,
                source=p.source, recorded_at=p.recorded_at) for p in params],
        )

    def params_of(self, sn: str) -> list[ParamView]:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        return [ParamView(
            param_key=p.param_key, param_value=p.param_value, unit=p.unit,
            source=p.source, recorded_at=p.recorded_at)
            for p in self.params.list_by_serial_unit(su.id)]
