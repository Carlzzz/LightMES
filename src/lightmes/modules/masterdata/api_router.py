"""Masterdata JSON API routes: /api/masterdata/*"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_role
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.models import Bom, BomItem, Operation, Routing
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.schemas import (
    BomCreate, BomItemRead, BomRead,
    OperationRead,
    ProductCreate, ProductRead,
    RoutingCreate, RoutingRead,
)
from lightmes.modules.masterdata.service import MasterDataService

router = APIRouter(prefix="/api/masterdata", tags=["masterdata-api"])


class BomItemConsumeOpUpdate(BaseModel):
    consume_at_operation_seq: int | None


def _operation_read(db: Session, op: Operation) -> OperationRead:
    """序列化 Operation，并填充 allowed_work_station_ids。"""
    read = OperationRead.model_validate(op)
    read.allowed_work_station_ids = [
        ws.id for ws in MasterDataQueryService(db).get_allowed_work_stations(op.id)
    ]
    return read


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    data: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> ProductRead:
    try:
        product = MasterDataService(db).create_product(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ProductRead.model_validate(product)


@router.get("/products", response_model=list[ProductRead])
def list_products(db: Session = Depends(get_db)) -> list[ProductRead]:
    products = MasterDataService(db).products.list_all()
    return [ProductRead.model_validate(p) for p in products]


@router.post("/routings", response_model=RoutingRead, status_code=status.HTTP_201_CREATED)
def create_routing(
    data: RoutingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
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
        operations=[_operation_read(db, o) for o in operations],
    )


@router.get("/routings", response_model=list[RoutingRead])
def list_routings(db: Session = Depends(get_db)) -> list[RoutingRead]:
    svc = MasterDataService(db)
    routings = svc.routings.list_all()
    return [
        RoutingRead(
            id=r.id, code=r.code, name=r.name,
            product_id=r.product_id, version=r.version,
            status=r.status,
            source=r.source, erp_ref=r.erp_ref, synced_at=r.synced_at,
            operations=[_operation_read(db, o) for o in svc.routings.operations_of(r.id)],
        )
        for r in routings
    ]


@router.get("/routings/{routing_id}", response_model=RoutingRead)
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
        operations=[_operation_read(db, o) for o in operations],
    )


@router.post("/boms", response_model=BomRead, status_code=status.HTTP_201_CREATED)
def create_bom(
    data: BomCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
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


@router.get("/boms/{bom_id}", response_model=BomRead)
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


@router.patch("/bom-items/{item_id}/consume-op")
def update_bom_item_consume_op(
    item_id: int,
    data: BomItemConsumeOpUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> dict:
    """更新单行 BOM 的 consume_at_operation_seq（仅 admin/supervisor）。

    若该 BOM 的 Product 有 active Routing，seq 必须属于该 Routing 的某个 op。
    """
    item = db.get(BomItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"BOM 行不存在: {item_id}")
    if data.consume_at_operation_seq is not None:
        bom = db.get(Bom, item.bom_id)
        routing = db.execute(
            select(Routing).where(
                Routing.product_id == bom.product_id,
                Routing.status == "active",
            )
        ).scalar_one_or_none()
        if routing is None:
            raise HTTPException(
                status_code=400, detail="该成品无 active Routing，无法指定消耗工序")
        valid_seqs = {op.seq for op in db.execute(
            select(Operation).where(Operation.routing_id == routing.id)
        ).scalars().all()}
        if data.consume_at_operation_seq not in valid_seqs:
            raise HTTPException(
                status_code=400,
                detail=f"工序 seq {data.consume_at_operation_seq} 不属于该成品 active Routing")
    item.consume_at_operation_seq = data.consume_at_operation_seq
    db.flush()
    db.commit()
    return {"ok": True, "item_id": item_id,
            "consume_at_operation_seq": item.consume_at_operation_seq}
