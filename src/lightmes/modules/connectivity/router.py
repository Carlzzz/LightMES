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


@router.get("/connectivity/connections/{conn_id}", response_class=HTMLResponse)
def connection_detail(
    request: Request,
    conn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    from lightmes.shared.errors import NotFoundError
    svc = ConnectivityService(db)
    try:
        conn = svc.get_connection(conn_id)
    except NotFoundError:
        return HTMLResponse("连接不存在", status_code=404)
    mqtt = svc.get_mqtt_for_connection(conn_id)
    topics = svc.list_topics(conn_id)
    messages = svc.list_recent_messages(conn_id, limit=100)
    # 组装 all_mappings：dict[topic -> list[TopicMapping]]
    # （Jinja 不能用 ORM 对象做 dict key；改成 list[tuple(topic, mappings)] 形式）
    all_mappings = [
        (t, svc.list_mappings(conn_id, t.id)) for t in topics
    ]
    return templates.TemplateResponse(
        request,
        "connectivity/connection_detail.html",
        {
            "conn": {
                "id": conn.id, "name": conn.name, "description": conn.description,
                "is_active": conn.is_active, "status": conn.status,
                "status_message": conn.status_message,
                "messages_received": conn.messages_received,
                "last_connected_at": conn.last_connected_at,
                "broker_host": mqtt.broker_host if mqtt else "—",
                "broker_port": mqtt.broker_port if mqtt else "—",
                "username": mqtt.username if mqtt else None,
                "use_tls": mqtt.use_tls if mqtt else False,
                "qos_default": mqtt.qos_default if mqtt else 0,
            },
            "topics": topics,
            "all_mappings": all_mappings,
            "messages": messages,
        },
    )


@router.post("/connectivity/connections/{conn_id}/topics", response_class=HTMLResponse)
def topic_add(
    request: Request,
    conn_id: int,
    topic_pattern: str = Form(...),
    payload_format: str = Form("json"),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.add_topic(conn_id, topic_pattern, payload_format, description or None)
        db.commit()
    except DomainError as e:
        return HTMLResponse(f"添加失败: {e.detail}", status_code=400)
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/toggle",
             response_class=HTMLResponse)
def topic_toggle(
    conn_id: int,
    topic_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.toggle_topic(conn_id, topic_id)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/delete",
             response_class=HTMLResponse)
def topic_delete(
    conn_id: int,
    topic_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.delete_topic(conn_id, topic_id)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post("/connectivity/connections/{conn_id}/topics/{topic_id}/mappings",
             response_class=HTMLResponse)
def mapping_add(
    conn_id: int,
    topic_id: int,
    action_type: str = Form(...),
    action_params: str = Form(""),
    field_path: str = Form(""),
    condition_expr: str = Form(""),
    priority: int = Form(100),
    description: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.add_mapping(
            conn_id=conn_id, topic_id=topic_id, action_type=action_type,
            action_params=action_params or None,
            field_path=field_path or None,
            condition_expr=condition_expr or None,
            priority=priority,
            description=description or None,
        )
        db.commit()
    except DomainError as e:
        return HTMLResponse(f"添加失败: {e.detail}", status_code=400)
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post(
    "/connectivity/connections/{conn_id}/topics/{topic_id}/mappings/{mid}/toggle",
    response_class=HTMLResponse,
)
def mapping_toggle(
    conn_id: int,
    topic_id: int,
    mid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.toggle_mapping(conn_id, topic_id, mid)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)


@router.post(
    "/connectivity/connections/{conn_id}/topics/{topic_id}/mappings/{mid}/delete",
    response_class=HTMLResponse,
)
def mapping_delete(
    conn_id: int,
    topic_id: int,
    mid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    svc.delete_mapping(conn_id, topic_id, mid)
    db.commit()
    return RedirectResponse(url=f"/connectivity/connections/{conn_id}", status_code=303)
