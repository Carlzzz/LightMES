from pathlib import Path
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import require_login, current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.schemas import GenealogyView, ParentRef
from lightmes.modules.trace.trace_service import TraceService
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import DomainError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _parent_sn(db: Session, parent_sn_id: int) -> str:
    """反查片段要展示成品 SN；ParentRef 只带父件内部 id，故按 id 解析 SN。"""
    su = SerialUnitRepository(db).get(parent_sn_id)
    return su.sn if su else str(parent_sn_id)


def _where_used_rows(db: Session, parents: list[ParentRef]) -> str:
    """反查（组件SN/批次→成品）片段；所有插值经 markupsafe.escape() 防 XSS。"""
    return "".join(
        f"<li>成品 #{escape(_parent_sn(db, p.parent_sn_id))} "
        f"({escape(p.component_ref)}) [{escape(p.status)}]</li>"
        for p in parents)


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
    # 手写片段：所有插值一律经 markupsafe.escape() 防 XSS。
    svc = TraceService(db)
    try:
        if query_type == "genealogy":
            view = svc.genealogy_of(value)
            rows = "".join(
                f"<li>{escape(c.component_type)}: {escape(c.component_ref)} "
                f"x{c.qty} [{escape(c.status)}]</li>"
                for c in view.components)
            html = f"<p>成品 {escape(view.sn)} 组件:</p><ul>{rows}</ul>"
        elif query_type == "where_used_sn":
            parents = svc.where_used(component_sn=value)
            rows = _where_used_rows(db, parents)
            html = f"<p>组件 {escape(value)} 装入:</p><ul>{rows}</ul>"
        elif query_type == "where_used_batch":
            parents = svc.where_used(component_batch_no=value)
            rows = _where_used_rows(db, parents)
            html = f"<p>批次 {escape(value)} 装入:</p><ul>{rows}</ul>"
        else:  # history
            h = svc.history_of(value)
            passes = "".join(
                f"<li>工序#{p.routing_step_id} 工位#{p.station_id} "
                f"{escape(p.result)} {p.pass_time}</li>"
                for p in h.passes)
            comps = "".join(
                f"<li>{escape(c.component_ref)} [{escape(c.status)}]</li>"
                for c in h.components)
            html = f"<p>SN {escape(h.sn)} 履历:</p><ul>{passes}</ul><p>组件:</p><ul>{comps}</ul>"
    except DomainError as e:
        return HTMLResponse(f'<div style="color:red">✗ {escape(e.detail)}</div>')
    return HTMLResponse(html)


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
        db.rollback()  # 吞异常前回滚（P1b 确立的约定）
        return HTMLResponse(f'<div style="color:red">✗ {escape(e.detail)}</div>')
    return HTMLResponse(
        f'<div style="color:green">✓ {escape(su.sn)} '
        f'已返工至工序 {su.current_operation_seq}</div>')
