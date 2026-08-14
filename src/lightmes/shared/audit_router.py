from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import html_role_guard, require_role
from lightmes.modules.auth.models import User
from lightmes.shared.audit import AuditLog

audit_router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    entity_type: str
    entity_id: int | None
    action: str
    before_state: dict | None
    after_state: dict | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    updated_at: datetime


def _query_logs(
    db: Session,
    entity_type: str | None,
    user_id: int | None,
    action: str | None,
    limit: int,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return list(db.execute(stmt.limit(limit)).scalars().all())


@audit_router.get("/system/audit-logs", response_class=HTMLResponse)
def audit_logs_page(
    request: Request,
    entity_type: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    limit: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _, guard = html_role_guard(request, db, "admin")
    if guard is not None:
        return guard

    parsed_user_id = int(user_id) if user_id and user_id.strip().isdigit() else None
    parsed_limit = int(limit) if limit and limit.strip().isdigit() else 100
    parsed_limit = min(max(parsed_limit, 1), 1000)
    logs = _query_logs(
        db,
        entity_type or None,
        parsed_user_id,
        action or None,
        parsed_limit,
    )
    return templates.TemplateResponse(
        request,
        "system/audit_logs.html",
        {
            "logs": logs,
            "entity_type": entity_type or "",
            "user_id": user_id or "",
            "action": action or "",
            "limit": parsed_limit,
        },
    )


@audit_router.get("/api/system/audit-logs", response_model=list[AuditLogRead])
def api_audit_logs(
    entity_type: str | None = Query(None),
    user_id: int | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[AuditLogRead]:
    logs = _query_logs(db, entity_type, user_id, action, limit)
    return [AuditLogRead.model_validate(log) for log in logs]
