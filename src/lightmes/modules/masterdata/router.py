from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.masterdata.schemas import (
    ProductCreate,
    ProductRead,
    RoutingCreate,
    RoutingRead,
    RoutingStepRead,
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


@router.post(
    "/api/masterdata/routings",
    response_model=RoutingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_routing(
    data: RoutingCreate, db: Session = Depends(get_db)
) -> RoutingRead:
    svc = MasterDataService(db)
    try:
        routing = svc.create_routing(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    steps = svc.routings.steps_of(routing.id)
    return RoutingRead(
        id=routing.id, code=routing.code, name=routing.name,
        product_id=routing.product_id, version=routing.version,
        status=routing.status,
        steps=[RoutingStepRead.model_validate(s) for s in steps],
    )


@router.get("/api/masterdata/routings", response_model=list[RoutingRead])
def list_routings(db: Session = Depends(get_db)) -> list[RoutingRead]:
    svc = MasterDataService(db)
    routings = svc.routings.list_all()
    return [
        RoutingRead(
            id=r.id, code=r.code, name=r.name,
            product_id=r.product_id, version=r.version,
            status=r.status,
            steps=[RoutingStepRead.model_validate(s) for s in svc.routings.steps_of(r.id)],
        )
        for r in routings
    ]


@router.get("/api/masterdata/routings/{routing_id}", response_model=RoutingRead)
def get_routing(routing_id: int, db: Session = Depends(get_db)) -> RoutingRead:
    svc = MasterDataService(db)
    routing = svc.routings.get(routing_id)
    if routing is None:
        raise HTTPException(status_code=404, detail="路线不存在")
    steps = svc.routings.steps_of(routing.id)
    return RoutingRead(
        id=routing.id, code=routing.code, name=routing.name,
        product_id=routing.product_id, version=routing.version,
        status=routing.status,
        steps=[RoutingStepRead.model_validate(s) for s in steps],
    )
