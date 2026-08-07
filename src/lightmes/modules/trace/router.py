from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_login, current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.trace.schemas import GenealogyView, ParentRef
from lightmes.modules.trace.trace_service import TraceService
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/api/trace/genealogy/{sn}", response_model=GenealogyView)
def api_genealogy(
    sn: str, db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> GenealogyView:
    return TraceService(db).genealogy_of(sn)


@router.get("/api/trace/where-used", response_model=list[ParentRef])
def api_where_used(
    component_sn: str | None = None, component_batch_no: str | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(require_login),
) -> list[ParentRef]:
    return TraceService(db).where_used(
        component_sn=component_sn, component_batch_no=component_batch_no)


@router.get("/trace/query", response_class=HTMLResponse)
def query_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/query.html")


@router.post("/trace/query", response_class=HTMLResponse)
def query_submit(
    request: Request, query_type: str = Form(...), value: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    svc = TraceService(db)
    ctx: dict = {"request": request}
    try:
        if query_type == "genealogy":
            ctx["genealogy"] = svc.genealogy_of(value)
        elif query_type == "where_used_sn":
            ctx["parents"] = svc.where_used(component_sn=value)
            ctx["search_label"] = f"组件 SN {value}"
        elif query_type == "where_used_batch":
            ctx["parents"] = svc.where_used(component_batch_no=value)
            ctx["search_label"] = f"批次 {value}"
        elif query_type == "params":
            h = svc.history_of(value)
            ctx["history"] = h
            ctx["params_only"] = True
        else:  # history
            ctx["history"] = svc.history_of(value)
    except DomainError as e:
        ctx["error"] = e.detail
    return templates.TemplateResponse(request, "trace/query_result.html", ctx)


@router.get("/trace/rework", response_class=HTMLResponse)
def rework_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/rework.html")


@router.post("/trace/rework", response_class=HTMLResponse)
def rework_submit(
    request: Request, sn: str = Form(...), target_seq: int = Form(...),
    reason: str = Form(""), db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        su = ReworkService(db).rework(
            sn, target_seq=target_seq, reason=reason or None, operator_id=user.id)
    except DomainError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {e.detail}</div>')
    return HTMLResponse(
        f'<div style="color:green">✓ {su.sn} '
        f'已返工至工序 {su.current_operation_seq}</div>')


@router.get("/trace/carrier-unbind", response_class=HTMLResponse)
def carrier_unbind_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/carrier_unbind.html")


@router.post("/trace/carrier-unbind", response_class=HTMLResponse)
def carrier_unbind_submit(
    request: Request, scan: str = Form(...), db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        su = CarrierService(db).unbind(scan, user.id)
    except DomainError as e:
        db.rollback()
        return HTMLResponse(f'<div style="color:red">✗ {e.detail}</div>')
    return HTMLResponse(
        f'<div style="color:green">✓ {su.sn} 已解绑载体码</div>')
