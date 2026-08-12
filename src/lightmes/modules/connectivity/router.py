from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_role
from lightmes.modules.auth.models import User
from lightmes.modules.connectivity.service import ConnectivityService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/connectivity", response_class=HTMLResponse)
def connectivity_index(request: Request) -> HTMLResponse:
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.get("/connectivity/connections", response_class=HTMLResponse)
def connections_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    connections = svc.list_connections()
    # 附加 mqtt 信息
    conn_views = []
    for c in connections:
        mqtt = svc.get_mqtt_for_connection(c.id)
        conn_views.append({
            "id": c.id, "name": c.name, "description": c.description,
            "is_active": c.is_active, "status": c.status,
            "status_message": c.status_message,
            "messages_received": c.messages_received,
            "last_connected_at": c.last_connected_at,
            "broker_host": mqtt.broker_host if mqtt else "—",
            "broker_port": mqtt.broker_port if mqtt else "—",
        })
    return templates.TemplateResponse(
        request, "connectivity/connections_list.html", {"connections": conn_views}
    )


@router.post("/connectivity/connections", response_class=HTMLResponse)
def connections_create(
    request: Request,
    name: str = Form(...),
    broker_host: str = Form(...),
    broker_port: int = Form(1883),
    username: str | None = Form(None),
    password: str | None = Form(None),
    use_tls: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.create_connection(
            name=name, broker_host=broker_host, broker_port=broker_port,
            username=username or None, password=password or None,
            use_tls=bool(use_tls))
        db.commit()
    except DomainError as e:
        return HTMLResponse(f"创建失败: {e.detail}", status_code=400)
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.post("/connectivity/connections/{conn_id}/activate", response_class=HTMLResponse)
def connections_activate(
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.activate_connection(conn_id)
    db.commit()
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.post("/connectivity/connections/{conn_id}/deactivate", response_class=HTMLResponse)
def connections_deactivate(
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.deactivate_connection(conn_id)
    db.commit()
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.post("/connectivity/connections/{conn_id}/delete", response_class=HTMLResponse)
def connections_delete(
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.delete_connection(conn_id)
    db.commit()
    return RedirectResponse(url="/connectivity/connections", status_code=303)
