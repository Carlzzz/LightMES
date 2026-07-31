from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product
from lightmes.modules.masterdata.repository import ProductRepository
from lightmes.modules.masterdata.schemas import ProductCreate


class MasterDataService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)

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
