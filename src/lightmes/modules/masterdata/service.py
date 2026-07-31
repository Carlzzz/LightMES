from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product, Routing, RoutingStep, Station
from lightmes.modules.masterdata.repository import (
    ProductRepository,
    RoutingRepository,
    StationRepository,
)
from lightmes.modules.masterdata.schemas import (
    ProductCreate,
    RoutingCreate,
    StationCreate,
)


class MasterDataService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)
        self.stations = StationRepository(db)
        self.routings = RoutingRepository(db)

    def create_product(self, data: ProductCreate) -> Product:
        if self.products.get_by_code(data.code) is not None:
            raise ValueError(f"产品编码已存在: {data.code}")
        product = Product(
            code=data.code,
            name=data.name,
            type=data.type,
            unit=data.unit,
            track_mode=data.track_mode,
            spec=data.spec,
        )
        return self.products.add(product)

    def create_station(self, data: StationCreate) -> Station:
        if self.stations.get_by_code(data.code) is not None:
            raise ValueError(f"工位编码已存在: {data.code}")
        station = Station(
            code=data.code,
            name=data.name,
            description=data.description,
            location=data.location,
        )
        return self.stations.add(station)

    def create_routing(self, data: RoutingCreate) -> Routing:
        if self.routings.get_by_code(data.code) is not None:
            raise ValueError(f"路线编码已存在: {data.code}")
        if self.products.get(data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        seqs = [s.seq for s in data.steps]
        if len(seqs) != len(set(seqs)):
            raise ValueError("工序 seq 不能重复")
        for step in data.steps:
            if self.stations.get(step.station_id) is None:
                raise ValueError(f"工位不存在: {step.station_id}")
        has_active = self.routings.get_active_by_product(data.product_id) is not None
        routing = Routing(
            code=data.code,
            name=data.name,
            product_id=data.product_id,
            version=data.version,
            status="inactive" if has_active else "active",
        )
        self.routings.add(routing)
        for step in data.steps:
            self.db.add(RoutingStep(
                routing_id=routing.id,
                seq=step.seq,
                station_id=step.station_id,
                name=step.name,
                is_mandatory=step.is_mandatory,
            ))
        self.db.flush()
        return routing
