from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_login
from lightmes.modules.auth.models import User
from lightmes.modules.production.schemas import (
    SnRuleCreate, SnRuleRead, OperationPassInput, OperationPassResult, WorkOrderCreate,
    WorkOrderRead,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.wip_service import WipService
from lightmes.shared.errors import DomainError, NotFoundError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.post(
    "/api/production/sn-rules",
    response_model=SnRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sn_rule(
    data: SnRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> SnRuleRead:
    svc = ProductionService(db)
    try:
        rule = svc.create_sn_rule(data)
    except ValueError as e:
        # pattern 非法与 code 冲突都走 ValueError；用 400 统一（code 冲突亦可接受）
        raise HTTPException(status_code=400, detail=str(e))
    return SnRuleRead.model_validate(rule)


@router.post(
    "/api/production/work-orders",
    response_model=WorkOrderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_work_order(
    data: WorkOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> WorkOrderRead:
    svc = ProductionService(db)
    try:
        wo = svc.create_work_order(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WorkOrderRead.model_validate(wo)


@router.post(
    "/api/production/work-orders/{work_order_id}/release",
    response_model=WorkOrderRead,
)
def release_work_order(
    work_order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> WorkOrderRead:
    svc = ProductionService(db)
    try:
        wo = svc.release_work_order(work_order_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return WorkOrderRead.model_validate(wo)


@router.get(
    "/api/production/work-orders/{work_order_id}", response_model=WorkOrderRead
)
def get_work_order(
    work_order_id: int, db: Session = Depends(get_db)
) -> WorkOrderRead:
    wo = ProductionService(db).work_orders.get(work_order_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return WorkOrderRead.model_validate(wo)


@router.post("/api/production/pass", response_model=OperationPassResult)
def api_pass_operation(
    data: OperationPassInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> OperationPassResult:
    data.operator_id = current_user.id
    return OperationPassService(db).pass_operation(data)  # DomainError→全局handler


@router.get("/production/scan", response_class=HTMLResponse)
def scan_page(
    request: Request, work_station_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "production/scan.html", {"work_station_id": work_station_id}
    )


@router.post("/production/scan", response_class=HTMLResponse)
def scan_submit(
    request: Request,
    work_station_id: int = Form(...),
    code_or_sn: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = OperationPassService(db)
    # 页面便利：先按 SN 试，NotFound 再当工单号试首站
    try:
        try:
            result = svc.pass_operation(OperationPassInput(
                work_station_id=work_station_id, sn=code_or_sn, operator_id=user.id))
        except NotFoundError:
            result = svc.pass_operation(OperationPassInput(
                work_station_id=work_station_id, work_order_code=code_or_sn,
                operator_id=user.id))
    except DomainError as e:
        # 事务中已 flush 的写入（如首站生成的 SerialUnit / SN 流水）必须回滚，
        # 否则 get_db 的成功路径会把它们 commit，留下孤儿数据。
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/scan_result.html", {"error": e.detail}
        )
    return templates.TemplateResponse(
        request, "production/partials/scan_result.html", {"result": result}
    )


@router.get("/production/sn-rules", response_class=HTMLResponse)
def sn_rules_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rules = ProductionService(db).sn_rules.list_all()
    return templates.TemplateResponse(
        request, "production/sn_rules.html", {"rules": rules}
    )


@router.post("/production/sn-rules", response_class=HTMLResponse)
def sn_rules_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    pattern: str = Form(...),
    seq_reset: str = Form("never"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = ProductionService(db)
    try:
        rule = svc.create_sn_rule(SnRuleCreate(
            code=code, name=name, pattern=pattern,
            seq_reset=seq_reset, product_id=None))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 5})
    return templates.TemplateResponse(
        request, "production/partials/sn_rule_row.html", {"r": rule}
    )


@router.get("/production/wip", response_class=HTMLResponse)
def wip_page(
    request: Request, work_order_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    items = WipService(db).wip_by_work_order(work_order_id) if work_order_id else []
    return templates.TemplateResponse(
        request, "production/wip.html",
        {"work_order_id": work_order_id, "items": items},
    )
