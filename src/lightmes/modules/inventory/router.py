from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import html_role_guard, require_login
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.models import MaterialLot, StockMovement
from lightmes.modules.production.repository import MaterialLotRepository
from lightmes.modules.inventory.schemas import MaterialLotCreate, MaterialLotRead

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/inventory/material-lots", response_class=HTMLResponse)
def material_lots_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator")
    if auth_response is not None:
        return auth_response
    lots = list(db.execute(select(MaterialLot).order_by(MaterialLot.id.desc())).scalars().all())
    return templates.TemplateResponse(
        request,
        "inventory/material_lots.html",
        {"lots": lots, "error": request.query_params.get("error")},
    )


@router.get("/inventory/stock-movements", response_class=HTMLResponse)
def stock_movements_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator")
    if auth_response is not None:
        return auth_response
    rows = db.execute(
        select(StockMovement, MaterialLot.code)
        .join(MaterialLot, MaterialLot.id == StockMovement.material_lot_id)
        .order_by(StockMovement.id.desc())
    ).all()
    movements = [
        {"movement": movement, "lot_code": lot_code}
        for movement, lot_code in rows
    ]
    return templates.TemplateResponse(
        request,
        "inventory/stock_movements.html",
        {"movements": movements},
    )


@router.get("/inventory/material-lots/{lot_id}", response_class=HTMLResponse)
def material_lot_detail_page(
    request: Request,
    lot_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator")
    if auth_response is not None:
        return auth_response
    lot = db.get(MaterialLot, lot_id)
    if lot is None:
        return HTMLResponse(f"物料批次不存在: {lot_id}", status_code=404)
    movements = list(
        db.execute(
            select(StockMovement)
            .where(StockMovement.material_lot_id == lot_id)
            .order_by(StockMovement.id.desc())
        ).scalars().all()
    )
    usage = MaterialLotRepository(db).usage_trace(lot_id)
    return templates.TemplateResponse(
        request,
        "inventory/material_lot_detail.html",
        {"lot": lot, "movements": movements, "usage": usage},
    )


@router.post("/inventory/material-lots", response_class=HTMLResponse)
def material_lots_create(
    request: Request,
    code: str = Form(...),
    product_id: int = Form(...),
    quantity: float = Form(...),
    supplier_lot: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor")
    if auth_response is not None:
        return auth_response
    try:
        MaterialLotService(db).receive(
            code=code,
            product_id=product_id,
            quantity=quantity,
            supplier_lot=supplier_lot or None,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        from urllib.parse import quote as _quote
        return RedirectResponse(
            url=f"/inventory/material-lots?error={_quote(str(e))}", status_code=303)
    return RedirectResponse(url="/inventory/material-lots", status_code=303)


@router.post("/inventory/material-lots/{lot_id}/release", response_class=HTMLResponse)
def material_lots_release(
    request: Request,
    lot_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor")
    if auth_response is not None:
        return auth_response
    lot = db.get(MaterialLot, lot_id)
    if lot is not None:
        MaterialLotService(db).release(lot.code)
        db.commit()
    return RedirectResponse(url="/inventory/material-lots", status_code=303)


@router.get("/api/inventory/material-lots", response_model=list[MaterialLotRead])
def api_material_lots(
    request: Request,
    db: Session = Depends(get_db),
) -> list[MaterialLot]:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator")
    if auth_response is not None:
        return auth_response
    return list(db.execute(select(MaterialLot).order_by(MaterialLot.id.desc())).scalars().all())


@router.get("/api/inventory/material-lots/{lot_id}/usage")
def api_material_lot_usage(
    request: Request,
    lot_id: int,
    db: Session = Depends(get_db),
) -> dict:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator")
    if auth_response is not None:
        return auth_response
    lot = db.get(MaterialLot, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail=f"material lot {lot_id} not found")
    usage = MaterialLotRepository(db).usage_trace(lot_id)
    return {
        "material_lot": {
            "id": lot.id,
            "code": lot.code,
            "product_id": lot.product_id,
        },
        "consumptions": [
            {
                "id": c.id,
                "batch_id": c.batch_id,
                "operation_record_id": c.operation_record_id,
                "quantity": float(c.quantity),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in usage["consumptions"]
        ],
        "work_orders": [
            {"id": wo.id, "code": wo.code}
            for wo in usage["work_orders"]
        ],
        "serial_units": [
            {
                "id": su.id,
                "sn": su.sn,
                "work_order_id": su.work_order_id,
            }
            for su in usage["serial_units"]
        ],
    }


@router.get("/api/inventory/stock-movements")
def api_stock_movements(
    db: Session = Depends(get_db),
    current_user=Depends(require_login),
) -> list[dict]:
    rows = db.execute(
        select(StockMovement, MaterialLot.code)
        .join(MaterialLot, MaterialLot.id == StockMovement.material_lot_id)
        .order_by(StockMovement.id.desc())
    ).all()
    return [
        {
            "id": movement.id,
            "material_lot_id": movement.material_lot_id,
            "lot_code": lot_code,
            "movement_type": movement.movement_type,
            "quantity": float(movement.quantity),
            "source_type": movement.source_type,
            "source_id": movement.source_id,
            "notes": movement.notes,
            "created_at": movement.created_at.isoformat() if movement.created_at else None,
        }
        for movement, lot_code in rows
    ]


@router.post("/api/inventory/material-lots", response_model=MaterialLotRead)
def api_material_lots_create(
    data: MaterialLotCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> MaterialLot:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor")
    if auth_response is not None:
        return auth_response
    lot = MaterialLotService(db).receive(
        code=data.code,
        product_id=data.product_id,
        quantity=data.quantity,
        supplier_lot=data.supplier_lot,
    )
    db.commit()
    return lot
