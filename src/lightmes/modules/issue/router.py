from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import (
    current_user_or_none, login_redirect, require_role,
)
from lightmes.modules.issue.linkify import issue_linkify
from lightmes.modules.issue.repository import (
    IssueActionRepository, IssueRepository, IssueTypeRepository,
)
from lightmes.modules.issue.service import IssueService
from lightmes.modules.production.models import SerialUnit
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)
# issue_linkify 已在 main.py 全局注册到 app 的 templates；
# 本 router 自建 templates 实例（不依赖 main.py 避免循环 import），需本地补注册。
templates.env.filters["issue_linkify"] = issue_linkify


def _user_role_name(user) -> str | None:
    """读取用户角色名（向后兼容包装）。新代码应直接用 auth.role_utils.user_role_name。"""
    from lightmes.modules.auth.role_utils import user_role_name
    return user_role_name(user)


def _is_privileged(user) -> bool:
    """supervisor / admin 看全部 Issue。"""
    from lightmes.modules.auth.role_utils import is_privileged
    return is_privileged(user)


def _back(path: str, error: str | None = None) -> Response:
    """POST 失败统一回跳：303 回原页带 ?error=，由页面顶部横幅呈现，
    避免裸文本替换整页。"""
    if error:
        sep = "&" if "?" in path else "?"
        return Response(
            status_code=303,
            headers={"Location": f"{path}{sep}error={quote(error)}"})
    return Response(status_code=303, headers={"Location": path})


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
        return login_redirect(request)

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

    serial_unit_ids = [i.serial_unit_id for i in issues if i.serial_unit_id]
    sn_map = {}
    if serial_unit_ids:
        sn_map = {
            su.id: su.sn
            for su in db.execute(
                select(SerialUnit).where(SerialUnit.id.in_(serial_unit_ids))
            ).scalars().all()
        }

    return templates.TemplateResponse(
        request, "issue/list.html",
        {"issues": issues,
         "sn_map": sn_map,
         "filters": {"status": status, "severity": severity,
                     "source": source, "search": search},
         "error": request.query_params.get("error")},
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
        return login_redirect(request)
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
        return _back("/issues", e.detail)
    # ANDON 提交后留在 station 页：返回小段 JS 触发 station view 刷新
    if source == "station_andon":
        return HTMLResponse(
            "<script>(function(){"
            "var f=document.getElementById('enter-form');"
            "if(f){htmx.trigger(f,'submit');}"
            "else if(window.location.pathname.indexOf('/production/station')===0){window.location.reload();}"
            "if(window.showErrorModal){window.showErrorModal('Issue #%d 已上报');}else{alert('Issue #%d 已上报');}"
            "})();</script>" % (issue.id, issue.id))
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
        request, "issue/types.html",
        {"types": types, "error": request.query_params.get("error")})


@router.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(
    request: Request,
    issue_id: int,
    db: Session = Depends(get_db),
):
    user = current_user_or_none(request, db)
    if user is None:
        return login_redirect(request)

    svc = IssueService(db)
    issue = svc.issues.get(issue_id)
    if issue is None:
        return Response(status_code=404, content="Issue 不存在")

    is_privileged = _is_privileged(user)
    # operator 只能看自己上报的
    if not is_privileged and issue.reported_by_id != user.id:
        return Response(status_code=403, content="无权查看")

    serial_unit_sn = None
    if issue.serial_unit_id:
        serial_unit = db.get(SerialUnit, issue.serial_unit_id)
        serial_unit_sn = serial_unit.sn if serial_unit else None

    from lightmes.modules.masterdata.models import WorkStation
    from lightmes.modules.production.models import Operation, WorkOrder
    wo = db.get(WorkOrder, issue.work_order_id) if issue.work_order_id else None
    station = db.get(WorkStation, issue.work_station_id) if issue.work_station_id else None
    operation = db.get(Operation, issue.operation_id) if issue.operation_id else None

    actions = IssueActionRepository(db).list_for_issue(issue_id)
    return templates.TemplateResponse(
        request, "issue/detail.html",
        {
            "issue": issue,
            "serial_unit_sn": serial_unit_sn,
            "wo": wo,
            "station": station,
            "operation": operation,
            "actions": actions,
            "is_privileged": is_privileged,
            "error": request.query_params.get("error"),
        },
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
        return _back(f"/issues/{issue_id}", e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


@router.post("/issues/{issue_id}/resolve")
def issue_resolve(
    request: Request, issue_id: int,
    root_cause: str = Form(...),
    containment_action: str = Form(...),
    disposition: str = Form(...),
    resolution_notes: str = Form(""),
    target_seq: int | None = Form(None),
    expected_repass_station_id: int | None = Form(None),
    user=Depends(require_role("supervisor", "admin")),
    db: Session = Depends(get_db),
):
    try:
        IssueService(db).resolve(
            issue_id, user.id,
            root_cause=root_cause, containment_action=containment_action,
            disposition=disposition,
            resolution_notes=resolution_notes or None,
            target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
        )
        db.commit()
    except DomainError as e:
        db.rollback()
        return _back(f"/issues/{issue_id}", e.detail)
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
        return _back(f"/issues/{issue_id}", e.detail)
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
        return _back(f"/issues/{issue_id}", e.detail)
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
        return _back(f"/issues/{issue_id}", e.detail)
    return Response(status_code=303, headers={"Location": f"/issues/{issue_id}"})


def _capa_transition(action_id: int, op: str, user, db: Session) -> Response:
    svc = IssueService(db)
    existing = svc.actions.get(action_id)
    issue_id = existing.issue_id if existing is not None else None
    back = f"/issues/{issue_id}" if issue_id is not None else "/issues"
    try:
        if op == "start":
            action = svc.start_action(action_id, user.id)
        elif op == "complete":
            action = svc.complete_action(action_id, user.id)
        elif op == "verify":
            action = svc.verify_action(action_id, user.id)
        else:
            return _back("/issues", "未知操作")
        db.commit()
    except DomainError as e:
        db.rollback()
        return _back(back, e.detail)
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
    from sqlalchemy.exc import IntegrityError
    from lightmes.modules.issue.models import IssueType
    try:
        db.add(IssueType(
            code=code.strip(), name=name.strip(), severity=severity,
            is_blocking=is_blocking, is_active=is_active,
            description=description or None))
        db.commit()
    except IntegrityError:
        db.rollback()
        return _back("/issues/types", f"code 已存在：{code.strip()}")
    except Exception as e:
        db.rollback()
        return _back("/issues/types", str(e))
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
        return _back("/issues/types", "类型不存在")
    t.is_active = not t.is_active
    db.commit()
    return Response(status_code=303, headers={"Location": "/issues/types"})
