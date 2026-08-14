from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import html_role_guard, require_role
from lightmes.modules.equipment.downtime_service import DowntimeService
from lightmes.modules.equipment.monitor_service import MonitorService
from lightmes.modules.equipment.models import (
    ALL_STATES, DowntimeReason, MachineTag, ProductionDowntime,
)
from lightmes.modules.equipment.oee_service import (
    OeeService, _shift_duration_seconds, compute_availability, compute_oee,
)
from lightmes.modules.equipment.schemas import TagCreate, TagUpdate
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.equipment.tag_service import TagService
from lightmes.modules.masterdata.models import WorkStation
from lightmes.modules.production.models import WorkOrder
from lightmes.modules.production.shift_service import ShiftService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _oee_rows(db: Session) -> list[dict]:
    now = datetime.now(timezone.utc)
    oee_svc = OeeService(db)
    sm = WorkstationStateMachine(db)
    shift_svc = ShiftService(db)

    stations = list(db.execute(
        select(WorkStation).where(WorkStation.is_active.is_(True))
        .order_by(WorkStation.line_id, WorkStation.seq)
    ).scalars().all())

    rows = []
    for ws in stations:
        shift = shift_svc.current_at(ws.line_id, now)
        duration = _shift_duration_seconds(shift) if shift is not None else 8 * 3600
        since = now - timedelta(seconds=duration)
        unplanned = oee_svc.unplanned_downtime_seconds(ws.id, since, now)
        availability = compute_availability(duration, unplanned)

        wo = db.execute(
            select(WorkOrder).where(WorkOrder.line_id == ws.line_id)
            .order_by(WorkOrder.id.desc()).limit(1)
        ).scalars().first()
        quality = oee_svc.quality_for_work_order(wo.id) if wo is not None else None

        cur = sm.current(ws.id)
        oee = compute_oee(availability, quality if quality is not None else 0.0)
        rows.append({
            "code": ws.code,
            "name": ws.name,
            "state": cur.state if cur is not None else "未采集",
            "availability": availability,
            "quality": quality,
            "oee": oee,
        })
    return rows


@router.get("/equipment/monitor", response_class=HTMLResponse)
def monitor_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    board = MonitorService(db).monitor_board()
    return templates.TemplateResponse(
        request, "equipment/monitor.html",
        {"board": board, "all_states": ALL_STATES})


@router.get("/equipment/monitor/partial", response_class=HTMLResponse)
def monitor_partial(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    board = MonitorService(db).monitor_board()
    return templates.TemplateResponse(
        request, "equipment/partials/monitor_board.html",
        {"board": board, "all_states": ALL_STATES})


@router.post("/equipment/monitor/{work_station_id}/transition")
def monitor_transition(work_station_id: int, state: str = Form(...),
                       db: Session = Depends(get_db),
                       _=Depends(require_role("admin", "supervisor"))):
    WorkstationStateMachine(db).transition(work_station_id, state, source="manual")
    db.commit()
    return RedirectResponse("/equipment/monitor", status_code=303)


@router.get("/equipment/downtimes", response_class=HTMLResponse)
def downtimes_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    rows = db.execute(
        select(ProductionDowntime, DowntimeReason.code, DowntimeReason.name, WorkStation.code)
        .outerjoin(DowntimeReason, ProductionDowntime.downtime_reason_id == DowntimeReason.id)
        .join(WorkStation, ProductionDowntime.work_station_id == WorkStation.id)
        .order_by(ProductionDowntime.started_at.desc())
    ).all()
    downtimes = [
        {"dt": dt, "reason_code": r_code, "reason_name": r_name, "station_code": ws_code}
        for dt, r_code, r_name, ws_code in rows
    ]
    reasons = list(db.execute(
        select(DowntimeReason).where(DowntimeReason.is_active.is_(True)).order_by(DowntimeReason.id)
    ).scalars().all())
    return templates.TemplateResponse(
        request, "equipment/downtimes.html",
        {"downtimes": downtimes, "reasons": reasons})


@router.get("/equipment/oee", response_class=HTMLResponse)
def oee_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    rows = _oee_rows(db)
    return templates.TemplateResponse(request, "equipment/oee.html", {"rows": rows})


@router.get("/equipment/tags", response_class=HTMLResponse)
def tags_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    tags = list(db.execute(select(MachineTag).order_by(MachineTag.id.desc())).scalars().all())
    return templates.TemplateResponse(request, "equipment/tags.html", {"tags": tags})


@router.get("/equipment/downtime-reasons", response_class=HTMLResponse)
def downtime_reasons_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    reasons = list(db.execute(select(DowntimeReason).order_by(DowntimeReason.id)).scalars().all())
    return templates.TemplateResponse(
        request, "equipment/downtime_reasons.html", {"reasons": reasons})


@router.post("/equipment/tags")
def create_tag(data: TagCreate, db: Session = Depends(get_db),
               _=Depends(require_role("admin", "supervisor"))):
    TagService(db).create(data)
    db.commit()
    return RedirectResponse("/equipment/tags", status_code=303)


@router.post("/equipment/tags/{tag_id}/delete")
def delete_tag(tag_id: int, db: Session = Depends(get_db),
               _=Depends(require_role("admin", "supervisor"))):
    TagService(db).delete(tag_id)
    db.commit()
    return RedirectResponse("/equipment/tags", status_code=303)


@router.post("/equipment/downtimes/{downtime_id}/reason")
def assign_downtime_reason(downtime_id: int, reason_id: int = Form(...),
                           notes: str = Form(""), db: Session = Depends(get_db),
                           _=Depends(require_role("admin", "supervisor"))):
    DowntimeService(db).assign_reason(downtime_id, reason_id, notes or None)
    db.commit()
    return RedirectResponse("/equipment/downtimes", status_code=303)
