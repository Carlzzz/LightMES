from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.models import Operation, WorkStation
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import OperationRecord, SerialUnit, WorkOrder
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import WipItem


class WipService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.query = MasterDataQueryService(db)

    def wip_by_work_order(self, work_order_id: int) -> list[WipItem]:
        """未完工单元：在制 + 返工中 + 隔离（与状态分布对齐，口径可对账）。"""
        units = self.serial_units.list_by_work_order(work_order_id)
        return [WipItem.model_validate(u) for u in units
                if u.status in ("in_process", "reworking", "quarantined")]

    def summary_by_work_order(self, work_order_id: int) -> dict:
        """工单级 WIP 概要：状态分布 + 完工进度。"""
        wo = self.db.get(WorkOrder, work_order_id)
        if wo is None:
            return {"work_order": None}
        units = self.serial_units.list_by_work_order(work_order_id)
        status_counts = Counter(u.status for u in units)
        total = len(units)
        produced = wo.produced_qty
        # 工序分布：每个 SN 当前所在工序序号 -> 数量
        seq_counts: dict[int, int] = {}
        for u in units:
            if u.status in ("in_process", "reworking"):
                seq_counts[u.current_operation_seq] = seq_counts.get(u.current_operation_seq, 0) + 1
        # 工序名映射
        operations = self.query.get_operations(wo.routing_id)
        op_map = {op.seq: op.name for op in operations}
        station_dist = [
            {"seq": seq, "op_name": op_map.get(seq, f"#{seq}"), "count": cnt}
            for seq, cnt in sorted(seq_counts.items())
        ]
        return {
            "work_order": wo,
            "total": total,
            "status_counts": dict(status_counts),
            "produced": produced,
            "planned_qty": wo.qty,
            "progress_pct": round(produced / wo.qty * 100, 1) if wo.qty > 0 else 0,
            "station_dist": station_dist,
        }
