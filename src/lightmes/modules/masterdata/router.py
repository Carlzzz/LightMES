from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_login
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.schemas import (
    BomCreate,
    BomItemRead,
    BomRead,
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
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.post(
    "/api/masterdata/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    data: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
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
    data: StationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
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
    data: RoutingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
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


@router.post(
    "/api/masterdata/boms",
    response_model=BomRead,
    status_code=status.HTTP_201_CREATED,
)
def create_bom(
    data: BomCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> BomRead:
    svc = MasterDataService(db)
    try:
        bom = svc.create_bom(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    items = svc.boms.items_of(bom.id)
    return BomRead(
        id=bom.id, product_id=bom.product_id, version=bom.version,
        status=bom.status,
        items=[BomItemRead.model_validate(i) for i in items],
    )


@router.get("/api/masterdata/boms/{bom_id}", response_model=BomRead)
def get_bom(bom_id: int, db: Session = Depends(get_db)) -> BomRead:
    svc = MasterDataService(db)
    bom = svc.boms.get(bom_id)
    if bom is None:
        raise HTTPException(status_code=404, detail="BOM 不存在")
    items = svc.boms.items_of(bom.id)
    return BomRead(
        id=bom.id, product_id=bom.product_id, version=bom.version,
        status=bom.status,
        items=[BomItemRead.model_validate(i) for i in items],
    )


@router.get("/masterdata/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    products = MasterDataService(db).products.list_all()
    return templates.TemplateResponse(
        request, "masterdata/products.html", {"products": products}
    )


@router.post("/masterdata/products", response_class=HTMLResponse)
def products_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    unit: str = Form("pcs"),
    track_mode: str = Form("none"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = MasterDataService(db)
    try:
        product = svc.create_product(ProductCreate(
            code=code, name=name, type=type, unit=unit, track_mode=track_mode))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 6})
    return templates.TemplateResponse(
        request, "masterdata/partials/product_row.html", {"product": product}
    )


@router.get("/masterdata/stations", response_class=HTMLResponse)
def stations_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    stations = MasterDataService(db).stations.list_all()
    return templates.TemplateResponse(
        request, "masterdata/stations.html", {"stations": stations}
    )


@router.post("/masterdata/stations", response_class=HTMLResponse)
def stations_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    location: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = MasterDataService(db)
    try:
        station = svc.create_station(StationCreate(
            code=code, name=name, location=location or None))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 4})
    return templates.TemplateResponse(
        request, "masterdata/partials/station_row.html", {"station": station}
    )
