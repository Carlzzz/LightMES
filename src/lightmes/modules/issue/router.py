from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_role
from lightmes.modules.issue.linkify import issue_linkify
from lightmes.modules.issue.repository import (
    IssueActionRepository, IssueRepository, IssueTypeRepository,
)
from lightmes.modules.issue.service import IssueService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)
# issue_linkify 已在 main.py 全局注册到 app 的 templates；
# 本 router 自建 templates 实例（不依赖 main.py 避免循环 import），需本地补注册。
templates.env.filters["issue_linkify"] = issue_linkify


def _user_role_name(user) -> str | None:
    """读取用户角色名：优先 role_obj.name，回退 legacy role 字段（兼容）。"""
    if user is None:
        return None
    if getattr(user, "role_obj", None) is not None:
        return user.role_obj.name
    return getattr(user, "role", None)


def _is_privileged(user) -> bool:
    """supervisor / admin 看全部 Issue。"""
    return _user_role_name(user) in ("supervisor", "admin")


@router.get("/issues", response_class=HTMLResponse)
def issue_list(
    request: Request,
    status: str = "",
    severity: str = "",
    source: str = "",
    search: str = "",
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})

    repo = IssueRepository(db)
    kwargs = {}
    if status:
        kwargs["statuses"] = status.split(",")
    if severity:
        kwargs["severities"] = severity.split(",")
    if source:
        kwargs["sources"] = source.split(",")
    if search:
        kwargs["search"] = search
    # operator 仅自己上报的；supervisor+ 全部
    if _user_role_name(user) == "operator":
        kwargs["reported_by_id"] = user.id
    issues = repo.list(**kwargs)

    return templates.TemplateResponse(
        request, "issue/list.html",
        {"issues": issues,
         "filters": {"status": status, "severity": severity,
                     "source": source, "search": search}},
    )


@router.post("/issues")
def issue_create(
    request: Request,
    issue_type_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    source: str = Form("manual"),
    work_station_id: int | None = Form(None),
    serial_unit_id: int | None = Form(None),
    work_order_id: int | None = Form(None),
    operation_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})
    try:
        issue = IssueService(db).create_issue(
            issue_type_id=issue_type_id,
            title=title,
            description=description or None,
            source=source,
            work_station_id=work_station_id or None,
            serial_unit_id=serial_unit_id or None,
            work_order_id=work_order_id or None,
            operation_id=operation_id or None,
            reported_by_id=user.id)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    # ANDON 提交后留在 station 页：返回小段 JS 触发 station view 刷新
    if source == "station_andon":
        return HTMLResponse(
            f"<script>htmx.trigger(document.getElementById('station-enter-form'), 'submit'); "
            f"window.showErrorModal('Issue #{issue.id} 已上报');</script>")
    return Response(status_code=303, headers={"Location": f"/issues/{issue.id}"})


# IssueType 字典管理（必须在 /issues/{issue_id} 之前注册，避免路径参数遮蔽）
@router.get("/issues/types", response_class=HTMLResponse)
def issue_types_page(
    request: Request,
    user=Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    types = IssueTypeRepository(db).list_all()
    return templates.TemplateResponse(
        request, "issue/types.html", {"types": types})


@router.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(
    request: Request,
    issue_id: int,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})

    svc = IssueService(db)
    issue = svc.issues.get(issue_id)
    if issue is None:
        return Response(status_code=404, content="Issue 不存在")

    is_privileged = _is_privileged(user)
    # operator 只能看自己上报的
    if not is_privileged and issue.reported_by_id != user.id:
        return Response(status_code=403, content="无权查看")

    actions = IssueActionRepository(db).list_for_issue(issue_id)
    return templates.TemplateResponse(
        request, "issue/detail.html",
        {"issue": issue, "actions": actions, "is_privileged": is_privileged},
    )


# ---- Lifecycle POST 端点 ----

@router.post("/issues/{issue_id}/acknowledge")
def issue_acknowledge(
    request: Request, issue_id: int,
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).acknowledge(issue_id, user.id)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/resolve")
def issue_resolve(
    request: Request, issue_id: int,
    root_cause: str = Form(...),
    containment_action: str = Form(...),
    disposition: str = Form(...),
    resolution_notes: str = Form(""),
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).resolve(
            issue_id, user.id,
            root_cause=root_cause, containment_action=containment_action,
            disposition=disposition,
            resolution_notes=resolution_notes or None,
        )
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/close")
def issue_close(
    request: Request, issue_id: int,
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).close(issue_id, user.id)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/reopen")
def issue_reopen(
    request: Request, issue_id: int,
    reason: str = Form(...),
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).reopen(issue_id, user.id, reason=reason)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


# ---- CAPA POST 端点 ----

@router.post("/issues/{issue_id}/actions")
def issue_add_action(
    request: Request, issue_id: int,
    type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    assigned_to_id: int | None = Form(None),
    due_date: str = Form(""),
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    from datetime import date as date_t
    try:
        IssueService(db).add_action(
            issue_id,
            type=type, title=title,
            description=description or None,
            assigned_to_id=assigned_to_id,
            due_date=date_t.fromisoformat(due_date) if due_date else None,
        )
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


def _capa_transition(action_id: int, op: str, user, db: Session) -> Response:
    svc = IssueService(db)
    try:
        if op == "start":
            action = svc.start_action(action_id, user.id)
        elif op == "complete":
            action = svc.complete_action(action_id, user.id)
        elif op == "verify":
            action = svc.verify_action(action_id, user.id)
        else:
            return Response(status_code=404)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=e.status_code, content=e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{action.issue_id}"})


@router.post("/issues/actions/{action_id}/start")
def capa_start(
    action_id: int,
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    return _capa_transition(action_id, "start", user, db)


@router.post("/issues/actions/{action_id}/complete")
def capa_complete(
    action_id: int,
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    return _capa_transition(action_id, "complete", user, db)


@router.post("/issues/actions/{action_id}/verify")
def capa_verify(
    action_id: int,
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    return _capa_transition(action_id, "verify", user, db)


# ---- IssueType 字典 CRUD ----
# GET /issues/types 已在文件上方注册（避免被 /issues/{issue_id} 遮蔽）

@router.post("/issues/types")
def issue_types_create(
    code: str = Form(...),
    name: str = Form(...),
    severity: str = Form(...),
    is_blocking: bool = Form(False),
    is_active: bool = Form(False),
    description: str = Form(""),
    user=Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    from lightmes.modules.issue.models import IssueType
    try:
        db.add(IssueType(
            code=code.strip(), name=name.strip(), severity=severity,
            is_blocking=is_blocking, is_active=is_active,
            description=description or None))
        db.commit()
    except Exception as e:
        db.rollback()
        return Response(status_code=400, content=str(e))
    return Response(status_code=303, headers={"Location": "/issues/types"})


@router.post("/issues/types/{type_id}/toggle-active")
def issue_types_toggle(
    type_id: int,
    user=Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    from lightmes.modules.issue.models import IssueType
    t = db.get(IssueType, type_id)
    if t is None:
        return Response(status_code=404, content="不存在")
    t.is_active = not t.is_active
    db.commit()
    return Response(status_code=303, headers={"Location": "/issues/types"})
