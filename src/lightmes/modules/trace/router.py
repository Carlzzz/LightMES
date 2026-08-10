from pathlib import Path
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_login, require_role
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.models import WorkStation
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.schemas import GenealogyView, ParentRef
from lightmes.modules.trace.trace_service import TraceService
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import DomainError, NotFoundError, ValidationError

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
    current_user: User = Depends(require_login),
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


@router.get("/trace/rework/allowed-stations", response_class=HTMLResponse)
def rework_allowed_stations(
    request: Request,
    sn: str = Query(...),
    target_seq: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> HTMLResponse:
    """返工页 target_seq onblur 触发：返回站位下拉片段。"""
    try:
        stations, first_repass_op = _resolve_rework_stations(db, sn, target_seq)
    except DomainError as e:
        return templates.TemplateResponse(
            request, "trace/partials/rework_allowed_stations.html",
            {"error": str(e.detail), "stations": [], "first_repass_op": None})
    return templates.TemplateResponse(
        request, "trace/partials/rework_allowed_stations.html",
        {"stations": stations, "first_repass_op": first_repass_op})


def _resolve_rework_stations(db: Session, sn: str, target_seq: int):
    """查首个 re-pass 工序 + 其 allowed 作业站列表。"""
    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    wo = db.get(WorkOrder, su.work_order_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {su.work_order_id}")
    query = MasterDataQueryService(db)
    operations = query.get_operations(wo.routing_id)
    first_repass_op = next((o for o in operations if o.seq > target_seq), None)
    if first_repass_op is None:
        raise ValidationError(f"target_seq {target_seq} 之后无工序可重做")
    allowed = query.get_allowed_work_stations(first_repass_op.id)
    station_ids = [w.id for w in allowed] or [first_repass_op.default_work_station_id]
    stations = list(db.execute(
        select(WorkStation).where(WorkStation.id.in_(station_ids))
    ).scalars().all())
    return stations, first_repass_op


@router.post("/trace/rework", response_class=HTMLResponse)
def rework_submit(
    request: Request, sn: str = Form(...), target_seq: int = Form(...),
    expected_repass_station_id: int = Form(...),
    reason: str = Form(""), db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    try:
        su = ReworkService(db).rework(
            sn, target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
            reason=reason or None, operator_id=user.id)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "trace/partials/error_result.html", {"error": e.detail})
    # 解析首个 re-pass 工序 + 站名用于成功提示
    try:
        stations, first_repass_op = _resolve_rework_stations(db, sn, target_seq)
        station = next((s for s in stations if s.id == expected_repass_station_id), None)
        station_name = station.name if station else f"#{expected_repass_station_id}"
    except DomainError:
        station_name = f"#{expected_repass_station_id}"
        first_repass_op = None
    return templates.TemplateResponse(
        request, "trace/partials/rework_success.html",
        {"su": su, "station_name": station_name,
         "target_seq": target_seq,
         "first_repass_op_seq": first_repass_op.seq if first_repass_op else None,
         "first_repass_op_name": first_repass_op.name if first_repass_op else None})


@router.get("/trace/carrier-unbind", response_class=HTMLResponse)
def carrier_unbind_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "trace/carrier_unbind.html")


@router.post("/trace/carrier-unbind", response_class=HTMLResponse)
def carrier_unbind_submit(
    request: Request, scan: str = Form(...), db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor", "operator")),
) -> HTMLResponse:
    try:
        su, carrier_code = CarrierService(db).unbind(scan, user.id)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "trace/partials/error_result.html", {"error": e.detail})
    return templates.TemplateResponse(
        request, "trace/partials/carrier_unbind_success.html",
        {"su": su, "carrier_code": carrier_code})
