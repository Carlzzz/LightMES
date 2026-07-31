from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.masterdata.schemas import (
    ProductCreate,
    ProductRead,
    StationCreate,
    StationRead,
)
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


@router.post(
    "/api/masterdata/stations",
    response_model=StationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_station(
    data: StationCreate, db: Session = Depends(get_db)
) -> StationRead:
    try:
        station = MasterDataService(db).create_station(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return StationRead.model_validate(station)


@router.get("/api/masterdata/stations", response_model=list[StationRead])
def list_stations(db: Session = Depends(get_db)) -> list[StationRead]:
    stations = MasterDataService(db).stations.list_all()
    return [StationRead.model_validate(s) for s in stations]
