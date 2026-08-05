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
    LineCreate,
    OperationCreate,
    OperationRead,
    ProductCreate,
    ProductRead,
    RoutingCreate,
    RoutingRead,
    WorkStationCreate,
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
    operations = svc.routings.operations_of(routing.id)
    return RoutingRead(
        id=routing.id, code=routing.code, name=routing.name,
        product_id=routing.product_id, version=routing.version,
        status=routing.status,
        source=routing.source, erp_ref=routing.erp_ref, synced_at=routing.synced_at,
        operations=[OperationRead.model_validate(o) for o in operations],
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
            source=r.source, erp_ref=r.erp_ref, synced_at=r.synced_at,
            operations=[OperationRead.model_validate(o) for o in svc.routings.operations_of(r.id)],
        )
        for r in routings
    ]


@router.get("/api/masterdata/routings/{routing_id}", response_model=RoutingRead)
def get_routing(routing_id: int, db: Session = Depends(get_db)) -> RoutingRead:
    svc = MasterDataService(db)
    routing = svc.routings.get(routing_id)
    if routing is None:
        raise HTTPException(status_code=404, detail="路线不存在")
    operations = svc.routings.operations_of(routing.id)
    return RoutingRead(
        id=routing.id, code=routing.code, name=routing.name,
        product_id=routing.product_id, version=routing.version,
        status=routing.status,
        source=routing.source, erp_ref=routing.erp_ref, synced_at=routing.synced_at,
        operations=[OperationRead.model_validate(o) for o in operations],
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
        source=bom.source, erp_ref=bom.erp_ref, synced_at=bom.synced_at,
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
        source=bom.source, erp_ref=bom.erp_ref, synced_at=bom.synced_at,
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
            {"error": str(e), "colspan": 7})
    return templates.TemplateResponse(
        request, "masterdata/partials/product_row.html", {"product": product}
    )


@router.get("/masterdata/lines", response_class=HTMLResponse)
def lines_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    lines = MasterDataService(db).lines.list_all()
    return templates.TemplateResponse(
        request, "masterdata/lines.html", {"lines": lines}
    )


@router.post("/masterdata/lines", response_class=HTMLResponse)
def lines_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = MasterDataService(db)
    try:
        line = svc.create_line(LineCreate(
            code=code, name=name, description=description or None))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 4})
    return templates.TemplateResponse(
        request, "masterdata/partials/line_row.html", {"line": line}
    )


@router.get("/masterdata/work-stations", response_class=HTMLResponse)
def work_stations_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    svc = MasterDataService(db)
    work_stations = svc.work_stations.list_all()
    lines = svc.lines.list_all()
    return templates.TemplateResponse(
        request, "masterdata/work_stations.html",
        {"work_stations": work_stations, "lines": lines}
    )


@router.post("/masterdata/work-stations", response_class=HTMLResponse)
def work_stations_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    line_id: int = Form(...),
    seq: int = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = MasterDataService(db)
    try:
        ws = svc.create_work_station(WorkStationCreate(
            code=code, name=name, line_id=line_id, seq=seq))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 5})
    return templates.TemplateResponse(
        request, "masterdata/partials/work_station_row.html", {"ws": ws}
    )


@router.get("/masterdata/routings", response_class=HTMLResponse)
def routings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    svc = MasterDataService(db)
    products = svc.products.list_all()
    work_stations = svc.work_stations.list_all()
    routings = svc.routings.list_all()
    return templates.TemplateResponse(
        request, "masterdata/routings.html",
        {"products": products, "work_stations": work_stations, "routings": routings}
    )


@router.post("/masterdata/routings", response_class=HTMLResponse)
def routings_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    product_id: int = Form(...),
    op_seq: list[str] = Form(default=[]),
    op_code: list[str] = Form(default=[]),
    op_name: list[str] = Form(default=[]),
    op_ws: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    operations = []
    for seq, c, n, ws in zip(op_seq, op_code, op_name, op_ws):
        if not c.strip() or not ws.strip():
            continue  # 空工序行忽略
        operations.append(OperationCreate(
            seq=int(seq), code=c.strip(), name=n.strip(),
            default_work_station_id=int(ws)))
    svc = MasterDataService(db)
    try:
        routing = svc.create_routing(RoutingCreate(
            code=code, name=name, product_id=product_id, operations=operations))
    except ValueError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 6})
    return templates.TemplateResponse(
        request, "masterdata/partials/routing_result.html", {"routing": routing}
    )


@router.get("/masterdata/boms", response_class=HTMLResponse)
def boms_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    boms = MasterDataService(db).boms.list_all()
    return templates.TemplateResponse(
        request, "masterdata/boms.html", {"boms": boms}
    )


