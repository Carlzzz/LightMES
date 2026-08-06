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

    def get_line(self, line_id: int) -> Line | None:
        return self._lines.get(line_id)

    def get_work_station(self, work_station_id: int) -> WorkStation | None:
        return self._work_stations.get(work_station_id)

    def list_work_stations(self) -> list[WorkStation]:
        return self._work_stations.list_all()
