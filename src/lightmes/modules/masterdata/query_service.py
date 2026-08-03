from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product, Routing, RoutingStep
from lightmes.modules.masterdata.repository import RoutingRepository


class MasterDataQueryService:
    """跨模块只读查询 facade。下游模块只调本类，不直接引用 masterdata repository/models。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._routings = RoutingRepository(db)

    def get_product(self, product_id: int) -> Product | None:
        return self.db.get(Product, product_id)

    def get_routing(self, routing_id: int) -> Routing | None:
        return self.db.get(Routing, routing_id)

    def get_ordered_steps(self, routing_id: int) -> list[RoutingStep]:
        return self._routings.steps_of(routing_id)
