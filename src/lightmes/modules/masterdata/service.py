from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product, Station
from lightmes.modules.masterdata.repository import ProductRepository, StationRepository
from lightmes.modules.masterdata.schemas import ProductCreate, StationCreate


class MasterDataService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)
        self.stations = StationRepository(db)

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
