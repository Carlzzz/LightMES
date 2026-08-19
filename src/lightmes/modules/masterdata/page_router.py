"""Masterdata HTML page routes: /masterdata/*"""
from itertools import zip_longest
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, html_role_guard, login_redirect
from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.models import Routing
from lightmes.modules.masterdata.schemas import (
    BomCreate, LineCreate, OperationCreate, ProductCreate,
    RoutingCreate, SkillCreate, WorkStationCreate,
)
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.masterdata.skill_service import SkillService

router = APIRouter(tags=["masterdata-pages"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _login_guard(request: Request, db: Session) -> Response | None:
    """Return a 401 redirect if not logged in, else None."""
    if current_user_or_none(request, db) is None:
        return login_redirect(request)
    return None


def _admin_guard(request: Request, db: Session) -> Response | None:
    _, response = html_role_guard(request, db, "admin")
    return response


def _redirect(path: str, error: str | None = None) -> RedirectResponse:
    from urllib.parse import quote
    if error:
        sep = "&" if "?" in path else "?"
        return RedirectResponse(f"{path}{sep}error={quote(error)}", status_code=303)
    return RedirectResponse(path, status_code=303)


# ---- Products ----

@router.get("/masterdata/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    products = MasterDataService(db).products.list_all()
    return templates.TemplateResponse(
        request, "masterdata/products.html",
        {"products": products, "error": request.query_params.get("error")}
    )


@router.post("/masterdata/products", response_class=HTMLResponse)
def products_create_page(
    request: Request,
    code: str = Form(...), name: str = Form(...), type: str = Form(...),
    unit: str = Form("pcs"), track_mode: str = Form("none"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    svc = MasterDataService(db)
    try:
        svc.create_product(ProductCreate(
            code=code, name=name, type=type, unit=unit, track_mode=track_mode))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/products", str(e))
    return _redirect("/masterdata/products")


@router.post("/masterdata/products/{product_id}/update", response_class=HTMLResponse)
def products_update_page(
    product_id: int, request: Request,
    code: str = Form(...), name: str = Form(...), type: str = Form(...),
    unit: str = Form("pcs"), track_mode: str = Form("none"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).update_product(
            product_id, code=code, name=name, type=type,
            unit=unit, track_mode=track_mode)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/products", str(e))
    return _redirect("/masterdata/products")


@router.post("/masterdata/products/{product_id}/delete", response_class=HTMLResponse)
def products_delete_page(
    product_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).delete_product(product_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/products", str(e))
    return _redirect("/masterdata/products")


# ---- Lines ----

@router.get("/masterdata/lines", response_class=HTMLResponse)
def lines_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    lines = MasterDataService(db).lines.list_all()
    return templates.TemplateResponse(
        request, "masterdata/lines.html",
        {"lines": lines, "error": request.query_params.get("error")}
    )


@router.post("/masterdata/lines", response_class=HTMLResponse)
def lines_create_page(
    request: Request,
    code: str = Form(...), name: str = Form(...), description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    svc = MasterDataService(db)
    try:
        svc.create_line(LineCreate(
            code=code, name=name, description=description or None))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/lines", str(e))
    return _redirect("/masterdata/lines")


@router.post("/masterdata/lines/{line_id}/update", response_class=HTMLResponse)
def lines_update_page(
    line_id: int, request: Request,
    code: str = Form(...), name: str = Form(...), description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).update_line(
            line_id, code=code, name=name, description=description or None)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/lines", str(e))
    return _redirect("/masterdata/lines")


@router.post("/masterdata/lines/{line_id}/delete", response_class=HTMLResponse)
def lines_delete_page(
    line_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).delete_line(line_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/lines", str(e))
    return _redirect("/masterdata/lines")


# ---- Skills ----

@router.get("/masterdata/skills", response_class=HTMLResponse)
def skills_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    skills = SkillService(db).list_skills()
    return templates.TemplateResponse(
        request, "masterdata/skills.html",
        {"skills": skills, "error": request.query_params.get("error")}
    )


@router.post("/masterdata/skills", response_class=HTMLResponse)
def skills_create_page(
    request: Request,
    code: str = Form(...), name: str = Form(...), max_level: int = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    svc = SkillService(db)
    try:
        svc.create_skill(SkillCreate(
            code=code, name=name, max_level=max_level,
            description=description or None))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/skills", str(e))
    return _redirect("/masterdata/skills")


@router.post("/masterdata/skills/{skill_id}/update", response_class=HTMLResponse)
def skills_update_page(
    skill_id: int, request: Request,
    code: str = Form(...), name: str = Form(...), max_level: int = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        SkillService(db).update_skill(
            skill_id, code=code, name=name, max_level=max_level,
            description=description or None)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/skills", str(e))
    return _redirect("/masterdata/skills")


@router.post("/masterdata/skills/{skill_id}/delete", response_class=HTMLResponse)
def skills_delete_page(
    skill_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        SkillService(db).delete_skill(skill_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/skills", str(e))
    return _redirect("/masterdata/skills")


# ---- Operator Skills ----

@router.get("/masterdata/operator-skills", response_class=HTMLResponse)
def operator_skills_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = SkillService(db)
    operator_skills = svc.list_operator_skills()
    users = UserRepository(db).list_all()
    skills = svc.list_skills()
    return templates.TemplateResponse(
        request, "masterdata/operator_skills.html",
        {"operator_skills": operator_skills, "users": users, "skills": skills,
         "user_map": {u.id: u for u in users}, "skill_map": {s.id: s for s in skills},
         "error": request.query_params.get("error")}
    )


@router.post("/masterdata/operator-skills", response_class=HTMLResponse)
def operator_skills_create_page(
    request: Request,
    user_id: int = Form(...), skill_id: int = Form(...), level: int = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    svc = SkillService(db)
    try:
        svc.set_operator_skill(user_id, skill_id, level)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/operator-skills", str(e))
    return _redirect("/masterdata/operator-skills")


@router.post("/masterdata/operator-skills/{os_id}/delete", response_class=HTMLResponse)
def operator_skills_delete_page(
    os_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        SkillService(db).delete_operator_skill(os_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/operator-skills", str(e))
    return _redirect("/masterdata/operator-skills")


# ---- Work Stations ----

@router.get("/masterdata/work-stations", response_class=HTMLResponse)
def work_stations_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = MasterDataService(db)
    work_stations = svc.work_stations.list_all()
    lines = svc.lines.list_all()
    line_map = {l.id: l for l in lines}
    return templates.TemplateResponse(
        request, "masterdata/work_stations.html",
        {"work_stations": work_stations, "lines": lines, "line_map": line_map,
         "error": request.query_params.get("error")}
    )


@router.post("/masterdata/work-stations", response_class=HTMLResponse)
def work_stations_create_page(
    request: Request,
    code: str = Form(...), name: str = Form(...),
    line_id: int = Form(...), seq: int = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    svc = MasterDataService(db)
    try:
        svc.create_work_station(WorkStationCreate(
            code=code, name=name, line_id=line_id, seq=seq))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/work-stations", str(e))
    return _redirect("/masterdata/work-stations")


@router.post("/masterdata/work-stations/{ws_id}/update", response_class=HTMLResponse)
def work_stations_update_page(
    ws_id: int, request: Request,
    code: str = Form(...), name: str = Form(...),
    line_id: int = Form(...), seq: int = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).update_work_station(
            ws_id, code=code, name=name, line_id=line_id, seq=seq)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/work-stations", str(e))
    return _redirect("/masterdata/work-stations")


@router.post("/masterdata/work-stations/{ws_id}/delete", response_class=HTMLResponse)
def work_stations_delete_page(
    ws_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).delete_work_station(ws_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/work-stations", str(e))
    return _redirect("/masterdata/work-stations")


# ---- Routings ----

@router.get("/masterdata/routings", response_class=HTMLResponse)
def routings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = MasterDataService(db)
    products = svc.products.list_all()
    product_map = {p.id: p for p in products}
    work_stations = svc.work_stations.list_all()
    routings = svc.routings.list_all()
    skills = SkillService(db).list_skills()
    return templates.TemplateResponse(
        request, "masterdata/routings.html",
        {"products": products, "product_map": product_map,
         "work_stations": work_stations, "routings": routings, "skills": skills}
    )


@router.post("/masterdata/routings", response_class=HTMLResponse)
def routings_create_page(
    request: Request,
    code: str = Form(...), name: str = Form(...), product_id: int = Form(...),
    op_seq: list[str] = Form(default=[]), op_code: list[str] = Form(default=[]),
    op_name: list[str] = Form(default=[]), op_ws: list[str] = Form(default=[]),
    op_allowed: list[str] = Form(default=[]),
    op_skill: list[str] = Form(default=[]), op_level: list[str] = Form(default=[]),
    op_req_material: list[str] = Form(default=[]),
    op_req_param: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    svc = MasterDataService(db)
    try:
        operations = []
        for seq, c, n, ws, allowed_str, sk_id, lvl, req_m, req_p in zip_longest(
            op_seq, op_code, op_name, op_ws, op_allowed, op_skill, op_level,
            op_req_material, op_req_param, fillvalue=""
        ):
            if not c.strip() or not ws.strip():
                continue
            if allowed_str.strip():
                allowed_ids = [int(x) for x in allowed_str.split(",")
                               if x.strip().isdigit()]
            else:
                allowed_ids = [int(ws)]
            allowed_ids = list(dict.fromkeys(allowed_ids))
            operations.append(OperationCreate(
                seq=int(seq), code=c.strip(), name=n.strip(),
                default_work_station_id=int(ws),
                allowed_work_station_ids=allowed_ids,
                required_skill_id=int(sk_id) if sk_id.strip() else None,
                required_level=int(lvl) if lvl.strip() else None,
                require_material_binding=(req_m == "true"),
                require_param_collection=(req_p == "true")))
        routing = svc.create_routing(RoutingCreate(
            code=code, name=name, product_id=product_id, operations=operations))
        db.commit()
    except ValueError as e:
        db.rollback()
        return _redirect("/masterdata/routings", str(e))
    return _redirect(f"/masterdata/routings/{routing.id}")


# ---- BOMs ----

@router.get("/masterdata/boms", response_class=HTMLResponse)
def boms_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = MasterDataService(db)
    boms = svc.boms.list_all()
    products = svc.products.list_all()
    product_map = {p.id: p for p in products}
    # 新建候选：成品类产品
    finished_products = [p for p in products if p.type == "finished"]
    return templates.TemplateResponse(
        request, "masterdata/boms.html",
        {"boms": boms, "product_map": product_map,
         "products": finished_products,
         "error": request.query_params.get("error")},
    )


@router.post("/masterdata/boms", response_class=HTMLResponse)
def boms_create(
    request: Request,
    product_id: int = Form(...),
    version: str = Form("1"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    from urllib.parse import quote as _quote
    try:
        bom = MasterDataService(db).create_bom(BomCreate(
            product_id=product_id, version=version, items=[]))
        db.commit()
    except ValueError as e:
        db.rollback()
        return RedirectResponse(
            url=f"/masterdata/boms?error={_quote(str(e))}", status_code=303)
    return RedirectResponse(url=f"/masterdata/boms/{bom.id}", status_code=303)


@router.post("/masterdata/boms/{bom_id}/toggle-active", response_class=HTMLResponse)
def bom_toggle_active(
    bom_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    from urllib.parse import quote as _quote
    bom = MasterDataService(db).boms.get(bom_id)
    if bom is None:
        return HTMLResponse("BOM 不存在", status_code=404)
    target = "inactive" if bom.status == "active" else "active"
    try:
        MasterDataService(db).set_bom_status(bom_id, target)
        db.commit()
    except ValueError as e:
        db.rollback()
        return RedirectResponse(
            url=f"/masterdata/boms/{bom_id}?error={_quote(str(e))}", status_code=303)
    return RedirectResponse(url="/masterdata/boms", status_code=303)


@router.post("/masterdata/boms/{bom_id}/delete", response_class=HTMLResponse)
def bom_delete(
    bom_id: int, request: Request, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    from urllib.parse import quote as _quote
    try:
        MasterDataService(db).delete_bom(bom_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return RedirectResponse(
            url=f"/masterdata/boms/{bom_id}?error={_quote(str(e))}", status_code=303)
    return RedirectResponse(url="/masterdata/boms", status_code=303)


# ---- BOM Detail ----

@router.get("/masterdata/boms/{bom_id}", response_class=HTMLResponse)
def bom_detail_page(
    bom_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = MasterDataService(db)
    query = MasterDataQueryService(db)
    bom = svc.boms.get(bom_id)
    if bom is None:
        return HTMLResponse(f"BOM 不存在: {bom_id}", status_code=404)
    items = svc.boms.items_of(bom.id)
    product = query.get_product(bom.product_id)
    routing = db.execute(
        select(Routing).where(
            Routing.product_id == bom.product_id,
            Routing.status == "active",
        )
    ).scalar_one_or_none()
    operations = []
    if routing is not None:
        operations = query.get_operations(routing.id)
    item_views = []
    for it in items:
        comp = query.get_product(it.component_product_id)
        item_views.append({
            "id": it.id,
            "component_code": comp.code if comp else str(it.component_product_id),
            "component_name": comp.name if comp else "",
            "track_mode": it.track_mode,
            "qty": float(it.qty),
            "consume_at_operation_seq": it.consume_at_operation_seq,
        })
    # 添加组件候选：非成品类产品
    component_candidates = [
        {"id": p.id, "label": f"{p.code} {p.name}（{p.track_mode}）"}
        for p in svc.products.list_all() if p.type != "finished"
    ]
    return templates.TemplateResponse(
        request, "masterdata/bom_detail.html", {
            "bom": {
                "id": bom.id, "version": bom.version, "status": bom.status,
                "source": bom.source, "product_code": product.code if product else "",
                "product_name": product.name if product else "",
                "items": item_views,
            },
            "routing": ({"id": routing.id, "code": routing.code, "name": routing.name}
                        if routing is not None else None),
            "operations": [{"seq": o.seq, "code": o.code, "name": o.name} for o in operations],
            "component_candidates": component_candidates,
            "error": request.query_params.get("error"),
        })


def _back_bom(bom_id: int, error: str) -> RedirectResponse:
    from urllib.parse import quote as _quote
    return RedirectResponse(
        url=f"/masterdata/boms/{bom_id}?error={_quote(error)}", status_code=303)


@router.post("/masterdata/boms/{bom_id}/items", response_class=HTMLResponse)
def bom_item_add(
    bom_id: int, request: Request,
    component_product_id: int = Form(...),
    qty: float = Form(...),
    consume_at_operation_seq: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    seq = int(consume_at_operation_seq) if consume_at_operation_seq.strip() else None
    try:
        MasterDataService(db).add_bom_item(
            bom_id, component_product_id=component_product_id,
            qty=qty, consume_at_operation_seq=seq)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _back_bom(bom_id, str(e))
    return RedirectResponse(url=f"/masterdata/boms/{bom_id}", status_code=303)


@router.post("/masterdata/boms/{bom_id}/items/{item_id}/update", response_class=HTMLResponse)
def bom_item_update(
    bom_id: int, item_id: int, request: Request,
    qty: float = Form(...),
    consume_at_operation_seq: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    seq = int(consume_at_operation_seq) if consume_at_operation_seq.strip() else None
    try:
        MasterDataService(db).update_bom_item(
            item_id, qty=qty, consume_at_operation_seq=seq)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _back_bom(bom_id, str(e))
    return RedirectResponse(url=f"/masterdata/boms/{bom_id}", status_code=303)


@router.post("/masterdata/boms/{bom_id}/items/{item_id}/delete", response_class=HTMLResponse)
def bom_item_delete(
    bom_id: int, item_id: int, request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).delete_bom_item(item_id)
        db.commit()
    except ValueError as e:
        db.rollback()
        return _back_bom(bom_id, str(e))
    return RedirectResponse(url=f"/masterdata/boms/{bom_id}", status_code=303)


# ---- Routing Detail ----

def _render_routing_detail(
    request: Request, db: Session, routing_id: int,
    error: str | None = None,
) -> HTMLResponse:
    svc = MasterDataService(db)
    query = MasterDataQueryService(db)
    routing = svc.routings.get(routing_id)
    if routing is None:
        return HTMLResponse("路线不存在", status_code=404)
    operations = svc.routings.operations_of(routing_id)
    op_views = [{"op": op, "allowed_ws_ids": [w.id for w in query.get_allowed_work_stations(op.id)]} for op in operations]
    product = svc.products.get(routing.product_id)
    # 工序物料消耗视图：产品 active BOM 各组件的 consume_at_operation_seq 分组
    bom = query.get_active_bom(routing.product_id)
    bom_view = None
    if bom is not None:
        bom_items = []
        for it in query.get_active_bom_items(routing.product_id):
            comp = query.get_product(it.component_product_id)
            bom_items.append({
                "component_code": comp.code if comp else str(it.component_product_id),
                "component_name": comp.name if comp else "",
                "track_mode": it.track_mode,
                "qty": float(it.qty),
                "consume_at_operation_seq": it.consume_at_operation_seq,
            })
        bom_view = {"id": bom.id, "version": bom.version, "items": bom_items}
    work_stations = svc.work_stations.list_all()
    skills = SkillService(db).list_skills()
    return templates.TemplateResponse(
        request, "masterdata/routing_detail.html",
        {"routing": routing, "product": product, "op_views": op_views,
         "work_stations": work_stations,
         "ws_map": {w.id: w for w in work_stations},
         "skills": skills,
         "skill_map": {s.id: s for s in skills},
         "active_bom": bom_view,
         "error": error})


def _render_head_card_fragment(
    request: Request, db: Session, routing_id: int,
) -> HTMLResponse:
    svc = MasterDataService(db)
    routing = svc.routings.get(routing_id)
    if routing is None:
        return HTMLResponse("路线不存在", status_code=404)
    product = svc.products.get(routing.product_id)
    return templates.TemplateResponse(
        request, "masterdata/partials/routing_head_card.html",
        {"routing": routing, "product": product})


def _parse_allowed(allowed_str: str, default_ws: int) -> list[int]:
    if allowed_str.strip():
        ids = [int(x) for x in allowed_str.split(",") if x.strip().isdigit()]
    else:
        ids = [default_ws]
    return list(dict.fromkeys(ids))


@router.get("/masterdata/routings/{routing_id}", response_class=HTMLResponse)
def routing_detail_page(
    request: Request, routing_id: int, db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    return _render_routing_detail(request, db, routing_id)


@router.post("/masterdata/routings/{routing_id}", response_class=HTMLResponse)
def routing_update_head(
    request: Request, routing_id: int, name: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).update_routing_head(routing_id, name)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(
            f'<div style="color:red">✗ {escape(str(e))}</div>',
            headers={"HX-Retarget": "#head-result", "HX-Reswap": "innerHTML"})
    return _render_head_card_fragment(request, db, routing_id)


@router.post("/masterdata/routings/{routing_id}/status", response_class=HTMLResponse)
def routing_set_status(
    request: Request, routing_id: int, status: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).set_routing_status(routing_id, status)
    except ValueError as e:
        db.rollback()
        return HTMLResponse(
            f'<div style="color:red">✗ {escape(str(e))}</div>',
            headers={"HX-Retarget": "#head-result", "HX-Reswap": "innerHTML"})
    return _render_head_card_fragment(request, db, routing_id)


@router.post("/masterdata/routings/{routing_id}/operations", response_class=HTMLResponse)
def routing_add_operation(
    request: Request, routing_id: int,
    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
    op_ws: int = Form(...), op_allowed: str = Form(""),
    op_skill: str = Form(""), op_level: str = Form(""),
    op_req_material: bool = Form(False), op_req_param: bool = Form(False),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    allowed_ids = _parse_allowed(op_allowed, op_ws)
    try:
        MasterDataService(db).add_operation(
            routing_id, seq=seq, code=code, name=name,
            default_work_station_id=op_ws, allowed_work_station_ids=allowed_ids,
            required_skill_id=int(op_skill) if op_skill.strip() else None,
            required_level=int(op_level) if op_level.strip() else None,
            is_mandatory=True,
            require_material_binding=op_req_material,
            require_param_collection=op_req_param)
    except ValueError as e:
        db.rollback()
        return _render_routing_detail(request, db, routing_id, error=str(e))
    return routing_detail_page(request, routing_id, db)


@router.post("/masterdata/routings/{routing_id}/operations/{operation_id}", response_class=HTMLResponse)
def routing_update_operation(
    request: Request, routing_id: int, operation_id: int,
    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
    op_ws: int = Form(...), op_allowed: str = Form(""),
    op_skill: str = Form(""), op_level: str = Form(""),
    op_req_material: bool = Form(False), op_req_param: bool = Form(False),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    allowed_ids = _parse_allowed(op_allowed, op_ws)
    try:
        MasterDataService(db).update_operation(
            operation_id, seq=seq, code=code, name=name,
            default_work_station_id=op_ws, allowed_work_station_ids=allowed_ids,
            required_skill_id=int(op_skill) if op_skill.strip() else None,
            required_level=int(op_level) if op_level.strip() else None,
            is_mandatory=True,
            require_material_binding=op_req_material,
            require_param_collection=op_req_param)
    except ValueError as e:
        db.rollback()
        return _render_routing_detail(request, db, routing_id, error=str(e))
    return routing_detail_page(request, routing_id, db)


@router.post("/masterdata/routings/{routing_id}/operations/{operation_id}/delete", response_class=HTMLResponse)
def routing_delete_operation(
    request: Request, routing_id: int, operation_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).delete_operation(operation_id)
    except ValueError as e:
        db.rollback()
        return _render_routing_detail(request, db, routing_id, error=str(e))
    return routing_detail_page(request, routing_id, db)


@router.post("/masterdata/routings/{routing_id}/delete")
def routing_delete(
    request: Request, routing_id: int, db: Session = Depends(get_db),
):
    if (r := _admin_guard(request, db)): return r
    try:
        MasterDataService(db).delete_routing(routing_id)
    except ValueError as e:
        db.rollback()
        return _render_routing_detail(request, db, routing_id, error=str(e))
    return Response(status_code=303, headers={"Location": "/masterdata/routings"})
