from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import (
    Bom,
    BomItem,
    Product,
    Routing,
    RoutingStep,
    Station,
)


class ProductRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, product: Product) -> Product:
        self.db.add(product)
        self.db.flush()
        return product

    def get(self, id: int) -> Product | None:
        return self.db.get(Product, id)

    def get_by_code(self, code: str) -> Product | None:
        return self.db.execute(
            select(Product).where(Product.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[Product]:
        return list(self.db.execute(select(Product)).scalars().all())


class StationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, station: Station) -> Station:
        self.db.add(station)
        self.db.flush()
        return station

    def get(self, id: int) -> Station | None:
        return self.db.get(Station, id)

    def get_by_code(self, code: str) -> Station | None:
        return self.db.execute(
            select(Station).where(Station.code == code)
        ).scalar_one_or_none()

    def list_all(self) -> list[Station]:
        return list(self.db.execute(select(Station)).scalars().all())


class RoutingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, routing: Routing) -> Routing:
        self.db.add(routing)
        self.db.flush()
        return routing

    def get(self, id: int) -> Routing | None:
        return self.db.get(Routing, id)

    def get_by_code(self, code: str) -> Routing | None:
        return self.db.execute(
            select(Routing).where(Routing.code == code)
        ).scalar_one_or_none()

    def get_active_by_product(self, product_id: int) -> Routing | None:
        return self.db.execute(
            select(Routing).where(
                Routing.product_id == product_id, Routing.status == "active"
            )
        ).scalar_one_or_none()

    def list_all(self) -> list[Routing]:
        return list(self.db.execute(select(Routing)).scalars().all())

    def steps_of(self, routing_id: int) -> list[RoutingStep]:
        return list(
            self.db.execute(
                select(RoutingStep)
                .where(RoutingStep.routing_id == routing_id)
                .order_by(RoutingStep.seq)
            ).scalars().all()
        )


class BomRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, bom: Bom) -> Bom:
        self.db.add(bom)
        self.db.flush()
        return bom

    def get(self, id: int) -> Bom | None:
        return self.db.get(Bom, id)

    def get_active_by_product(self, product_id: int) -> Bom | None:
        return self.db.execute(
            select(Bom).where(Bom.product_id == product_id, Bom.status == "active")
        ).scalar_one_or_none()

    def list_all(self) -> list[Bom]:
        return list(self.db.execute(select(Bom)).scalars().all())

    def items_of(self, bom_id: int) -> list[BomItem]:
        return list(
            self.db.execute(
                select(BomItem).where(BomItem.bom_id == bom_id)
            ).scalars().all()
        )
