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
from lightmes.modules.equipment.models import ALL_STATES, DowntimeReason, MachineTag
from lightmes.modules.equipment.schemas import TagCreate, TagUpdate
from lightmes.modules.equipment.state_machine import WorkstationStateMachine
from lightmes.modules.equipment.tag_service import TagService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


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
    reasons = list(db.execute(
        select(DowntimeReason).where(DowntimeReason.is_active.is_(True))
    ).scalars().all())
    return templates.TemplateResponse(
        request, "equipment/downtimes.html", {"reasons": reasons})


@router.get("/equipment/oee", response_class=HTMLResponse)
def oee_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor", "operator", "viewer")
    if auth_response is not None:
        return auth_response
    return templates.TemplateResponse(request, "equipment/oee.html", {})


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
