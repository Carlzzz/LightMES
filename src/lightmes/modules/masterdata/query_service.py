from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import (
    Bom,
    BomItem,
    Line,
    Operation,
    Product,
    Routing,
    WorkStation,
)
from lightmes.modules.masterdata.repository import (
    BomRepository,
    LineRepository,
    OperationWorkStationRepository,
    RoutingRepository,
    WorkStationRepository,
)


class MasterDataQueryService:
    """跨模块只读查询 facade。下游模块只调本类，不直接引用 masterdata repository/models。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._routings = RoutingRepository(db)
        self._boms = BomRepository(db)
        self._lines = LineRepository(db)
        self._work_stations = WorkStationRepository(db)
        self._op_ws = OperationWorkStationRepository(db)

    def get_product(self, product_id: int) -> Product | None:
        return self.db.get(Product, product_id)

    def get_routing(self, routing_id: int) -> Routing | None:
        return self.db.get(Routing, routing_id)

    def get_operations(self, routing_id: int) -> list[Operation]:
        return self._routings.operations_of(routing_id)

    def get_active_bom(self, product_id: int) -> Bom | None:
        return self._boms.get_active_by_product(product_id)

    def get_active_bom_items(self, product_id: int) -> list[BomItem]:
        bom = self._boms.get_active_by_product(product_id)
        if bom is None:
            return []
        return self._boms.items_of(bom.id)

    def get_bom_items_by_consume_op(
        self, product_id: int, op_seq: int,
    ) -> list[BomItem]:
        """返回 consume_at_operation_seq == op_seq 的 active BOM 行。

        NULL consume_at_operation_seq 不返回（兼容老数据，仅最终工序累积校验参与）。
        """
        bom = self._boms.get_active_by_product(product_id)
        if bom is None:
            return []
        return [i for i in self._boms.items_of(bom.id)
                if i.consume_at_operation_seq == op_seq]

    def get_line(self, line_id: int) -> Line | None:
        return self._lines.get(line_id)

    def get_work_station(self, work_station_id: int) -> WorkStation | None:
        return self._work_stations.get(work_station_id)

    def list_work_stations(self) -> list[WorkStation]:
        return self._work_stations.list_all()

    def get_allowed_work_stations(self, operation_id: int) -> list[WorkStation]:
        rows = self._op_ws.list_by_operation(operation_id)
        ws_ids = [r.work_station_id for r in rows]
        if not ws_ids:
            return []
        return [self._work_stations.get(i) for i in ws_ids
                if self._work_stations.get(i) is not None]

    def batch_allowed_work_stations(self, operation_ids: list[int]) -> dict[int, list[WorkStation]]:
        """批量查询多个工序的 allowed work stations，一次 DB 查询替代 N 次。"""
        op_to_ws_ids = self._op_ws.list_by_operation_ids(operation_ids)
        # 收集所有需要的 ws_id，一次性查
        all_ws_ids = set()
        for ids in op_to_ws_ids.values():
            all_ws_ids.update(ids)
        ws_map: dict[int, WorkStation] = {}
        for ws_id in all_ws_ids:
            ws = self._work_stations.get(ws_id)
            if ws is not None:
                ws_map[ws_id] = ws
        return {op_id: [ws_map[ws_id] for ws_id in ws_ids if ws_id in ws_map]
                for op_id, ws_ids in op_to_ws_ids.items()}
