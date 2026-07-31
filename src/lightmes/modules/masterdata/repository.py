from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Product


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
