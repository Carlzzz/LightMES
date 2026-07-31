from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.masterdata.schemas import ProductCreate, ProductRead
from lightmes.modules.masterdata.service import MasterDataService

router = APIRouter()


@router.post(
    "/api/masterdata/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    data: ProductCreate, db: Session = Depends(get_db)
) -> ProductRead:
    try:
        product = MasterDataService(db).create_product(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ProductRead.model_validate(product)


@router.get("/api/masterdata/products", response_model=list[ProductRead])
def list_products(db: Session = Depends(get_db)) -> list[ProductRead]:
    products = MasterDataService(db).products.list_all()
    return [ProductRead.model_validate(p) for p in products]
