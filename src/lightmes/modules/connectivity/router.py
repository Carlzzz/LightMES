from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_role
from lightmes.modules.auth.models import User
from lightmes.modules.connectivity.models import MachineConnection, MachineMessage
from lightmes.modules.connectivity.service import ConnectivityService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.get("/connectivity", response_class=HTMLResponse)
def connectivity_index(request: Request) -> HTMLResponse:
    return RedirectResponse(url="/connectivity/connections", status_code=303)


@router.get("/connectivity/dashboard", response_class=HTMLResponse)
def connectivity_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    """数采看板：总览所有连接 + 最近 20 条消息 + 最近 10 条错误。"""
    # 1. 协议分布（按 protocol 统计 active 连接数）
    proto_rows = db.execute(
        select(MachineConnection.protocol, func.count(MachineConnection.id))
        .where(MachineConnection.is_active.is_(True))
        .group_by(MachineConnection.protocol)
    ).all()
    protocol_counts = {row[0]: row[1] for row in proto_rows}
    protocol_views = [
        {"protocol": "mqtt", "label": "MQTT",
         "active": protocol_counts.get("mqtt", 0)},
        {"protocol": "opcua", "label": "OPC-UA",
         "active": protocol_counts.get("opcua", 0)},
        {"protocol": "modbus", "label": "Modbus TCP",
         "active": protocol_counts.get("modbus", 0)},
    ]

    # 2. 状态汇总（所有连接）
    status_rows = db.execute(
        select(MachineConnection.status, func.count(MachineConnection.id))
        .group_by(MachineConnection.status)
    ).all()
    status_counts = {row[0]: row[1] for row in status_rows}
    status_views = [
        {"key": "connected", "label": "已连接",
         "count": status_counts.get("connected", 0), "badge_class": "badge--ok"},
        {"key": "connecting", "label": "连接中",
         "count": status_counts.get("connecting", 0), "badge_class": "badge--warn"},
        {"key": "disconnected", "label": "未连接",
         "count": status_counts.get("disconnected", 0), "badge_class": ""},
        {"key": "error", "label": "错误",
         "count": status_counts.get("error", 0), "badge_class": "badge--danger"},
    ]

    # 3. 最近 20 条消息（所有连接） + connection 名字
    recent_msgs = db.execute(
        select(MachineMessage, MachineConnection.name)
        .join(
            MachineConnection,
            MachineConnection.id == MachineMessage.machine_connection_id,
        )
        .order_by(MachineMessage.received_at.desc())
        .limit(20)
    ).all()
    recent_views = [
        {
            "id": msg.id,
            "connection_name": conn_name,
            "topic": msg.topic,
            "received_at": msg.received_at,
            "processing_status": msg.processing_status,
            "parsed_data": msg.parsed_data,
            "actions_triggered": msg.actions_triggered or [],
            "raw_payload": msg.raw_payload,
        }
        for msg, conn_name in recent_msgs
    ]

    # 4. 最近 10 条错误消息
    error_msgs = db.execute(
        select(MachineMessage, MachineConnection.name)
        .join(
            MachineConnection,
            MachineConnection.id == MachineMessage.machine_connection_id,
        )
        .where(MachineMessage.processing_status == "error")
        .order_by(MachineMessage.received_at.desc())
        .limit(10)
    ).all()
    error_views = [
        {
            "id": msg.id,
            "connection_name": conn_name,
            "topic": msg.topic,
            "received_at": msg.received_at,
            "processing_error": msg.processing_error,
        }
        for msg, conn_name in error_msgs
    ]

    return templates.TemplateResponse(
        request,
        "connectivity/dashboard.html",
        {
            "protocol_views": protocol_views,
            "status_views": status_views,
            "total_connections": sum(status_counts.values()),
            "recent_messages": recent_views,
            "error_messages": error_views,
        },
    )


@router.get("/connectivity/connections", response_class=HTMLResponse)
def connections_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    connections = svc.list_connections()
    conn_views = []
    for c in connections:
        mqtt = svc.get_mqtt_for_connection(c.id)
        opcua = svc.get_opcua_for_connection(c.id)
        modbus = svc.get_modbus_for_connection(c.id)
        # 协议特定的展示端点
        if c.protocol == "mqtt" and mqtt:
            endpoint = f"{mqtt.broker_host}:{mqtt.broker_port}"
        elif c.protocol == "opcua" and opcua:
            endpoint = opcua.server_url
        elif c.protocol == "modbus" and modbus:
            endpoint = f"{modbus.host}:{modbus.port}"
        else:
            endpoint = "—"
        conn_views.append({
            "id": c.id, "name": c.name, "description": c.description,
            "protocol": c.protocol,
            "is_active": c.is_active, "status": c.status,
            "status_message": c.status_message,
            "messages_received": c.messages_received,
            "last_connected_at": c.last_connected_at,
            "endpoint": endpoint,
        })
    return templates.TemplateResponse(
        request, "connectivity/connections_list.html", {"connections": conn_views}
    )


@router.post("/connectivity/connections", response_class=HTMLResponse)
def connections_create(
    request: Request,
    name: str = Form(...),
    protocol: str = Form("mqtt"),
    description: str | None = Form(None),
    # MQTT fields
    broker_host: str | None = Form(None),
    broker_port: int = Form(1883),
    username: str | None = Form(None),
    password: str | None = Form(None),
    use_tls: bool = Form(False),
    keep_alive_seconds: int = Form(60),
    qos_default: int = Form(0),
    clean_session: bool = Form(True),
    # OPC-UA fields
    server_url: str | None = Form(None),
    security_mode: str = Form("none"),
    # Modbus fields
    host: str | None = Form(None),
    port: int = Form(502),
    slave_id: int = Form(1),
    # Shared fields
    poll_interval_seconds: int = Form(5),
    connect_timeout_seconds: int = Form(10),
    reconnect_delay_seconds: int = Form(5),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    svc = ConnectivityService(db)
    try:
        svc.create_connection(
            name=name, protocol=protocol, description=description or None,
            broker_host=broker_host or None, broker_port=broker_port,
            username=username or None, password=password or None,
            use_tls=bool(use_tls),
            keep_alive_seconds=keep_alive_seconds,
            qos_default=qos_default,
            clean_session=bool(clean_session),
            server_url=server_url or None,
            security_mode=security_mode,
            host=host or None, port=port, slave_id=slave_id,
            poll_interval_seconds=poll_interval_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            reconnect_delay_seconds=reconnect_delay_seconds,
        )
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
    opcua = svc.get_opcua_for_connection(conn_id)
    modbus = svc.get_modbus_for_connection(conn_id)
    topics = svc.list_topics(conn_id)
    messages = svc.list_recent_messages(conn_id, limit=100)
    all_mappings = [
        (t, svc.list_mappings(conn_id, t.id)) for t in topics
    ]
    # 协议特定端点展示
    if conn.protocol == "mqtt" and mqtt:
        endpoint = f"{mqtt.broker_host}:{mqtt.broker_port}"
    elif conn.protocol == "opcua" and opcua:
        endpoint = opcua.server_url
    elif conn.protocol == "modbus" and modbus:
        endpoint = f"{modbus.host}:{modbus.port}"
    else:
        endpoint = "—"
    return templates.TemplateResponse(
        request,
        "connectivity/connection_detail.html",
        {
            "conn": {
                "id": conn.id, "name": conn.name, "description": conn.description,
                "protocol": conn.protocol,
                "is_active": conn.is_active, "status": conn.status,
                "status_message": conn.status_message,
                "messages_received": conn.messages_received,
                "last_connected_at": conn.last_connected_at,
                "endpoint": endpoint,
                # MQTT 字段
                "username": mqtt.username if mqtt else None,
                "use_tls": mqtt.use_tls if mqtt else False,
                "qos_default": mqtt.qos_default if mqtt else 0,
                "keep_alive_seconds": mqtt.keep_alive_seconds if mqtt else 60,
                "clean_session": mqtt.clean_session if mqtt else True,
                # OPC-UA 字段
                "security_mode": opcua.security_mode if opcua else None,
                "poll_interval_seconds": (
                    opcua.poll_interval_seconds if opcua
                    else modbus.poll_interval_seconds if modbus else None
                ),
                # Modbus 字段
                "slave_id": modbus.slave_id if modbus else None,
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
