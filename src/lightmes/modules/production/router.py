from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_login
from lightmes.modules.auth.models import User
from lightmes.modules.production.schemas import (
    SnRuleCreate, SnRuleRead, OperationPassInput, OperationPassResult, WorkOrderCreate,
    WorkOrderRead, ComponentInput, ParamInput,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.station_service import StationService
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.wip_service import WipService
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.shared.errors import DomainError, NotFoundError, BusinessRuleError

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.post(
    "/api/production/sn-rules",
    response_model=SnRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sn_rule(
    data: SnRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> SnRuleRead:
    svc = ProductionService(db)
    try:
        rule = svc.create_sn_rule(data)
    except ValueError as e:
        # pattern 非法与 code 冲突都走 ValueError；用 400 统一（code 冲突亦可接受）
        raise HTTPException(status_code=400, detail=str(e))
    return SnRuleRead.model_validate(rule)


@router.post(
    "/api/production/work-orders",
    response_model=WorkOrderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_work_order(
    data: WorkOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> WorkOrderRead:
    svc = ProductionService(db)
    try:
        wo = svc.create_work_order(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WorkOrderRead.model_validate(wo)


@router.post(
    "/api/production/work-orders/{work_order_id}/release",
    response_model=WorkOrderRead,
)
def release_work_order(
    work_order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> WorkOrderRead:
    svc = ProductionService(db)
    try:
        wo = svc.release_work_order(work_order_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return WorkOrderRead.model_validate(wo)


@router.get(
    "/api/production/work-orders/{work_order_id}", response_model=WorkOrderRead
)
def get_work_order(
    work_order_id: int, db: Session = Depends(get_db)
) -> WorkOrderRead:
    wo = ProductionService(db).work_orders.get(work_order_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return WorkOrderRead.model_validate(wo)


@router.post("/api/production/pass", response_model=OperationPassResult)
def api_pass_operation(
    data: OperationPassInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> OperationPassResult:
    data.operator_id = current_user.id
    return OperationPassService(db).pass_operation(data)  # DomainError→全局handler


@router.get("/production/scan", response_class=HTMLResponse)
def scan_page(
    request: Request, work_station_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "production/scan.html", {"work_station_id": work_station_id}
    )


@router.post("/production/scan", response_class=HTMLResponse)
def scan_submit(
    request: Request,
    work_station_id: int = Form(...),
    code_or_sn: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = OperationPassService(db)
    # 页面便利：先按 SN 试，NotFound 再当工单号试首站
    try:
        try:
            result = svc.pass_operation(OperationPassInput(
                work_station_id=work_station_id, sn=code_or_sn, operator_id=user.id))
        except NotFoundError:
            result = svc.pass_operation(OperationPassInput(
                work_station_id=work_station_id, work_order_code=code_or_sn,
                operator_id=user.id))
    except DomainError as e:
        # 事务中已 flush 的写入（如首站生成的 SerialUnit / SN 流水）必须回滚，
        # 否则 get_db 的成功路径会把它们 commit，留下孤儿数据。
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/scan_result.html", {"error": e.detail}
        )
    return templates.TemplateResponse(
        request, "production/partials/scan_result.html", {"result": result}
    )


@router.get("/production/sn-rules", response_class=HTMLResponse)
def sn_rules_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rules = ProductionService(db).sn_rules.list_all()
    return templates.TemplateResponse(
        request, "production/sn_rules.html", {"rules": rules}
    )


@router.post("/production/sn-rules", response_class=HTMLResponse)
def sn_rules_create_page(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    pattern: str = Form(...),
    seq_reset: str = Form("never"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    svc = ProductionService(db)
    try:
        rule = svc.create_sn_rule(SnRuleCreate(
            code=code, name=name, pattern=pattern,
            seq_reset=seq_reset, product_id=None))
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html",
            {"error": str(e), "colspan": 5})
    return templates.TemplateResponse(
        request, "production/partials/sn_rule_row.html", {"r": rule}
    )


@router.get("/production/wip", response_class=HTMLResponse)
def wip_page(
    request: Request, work_order_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    items = WipService(db).wip_by_work_order(work_order_id) if work_order_id else []
    return templates.TemplateResponse(
        request, "production/wip.html",
        {"work_order_id": work_order_id, "items": items},
    )


@router.get("/production/station", response_class=HTMLResponse)
def station_page(
    request: Request, work_station_id: int = 0, db: Session = Depends(get_db)
) -> HTMLResponse:
    query = MasterDataQueryService(db)
    stations = query.list_work_stations()
    # 附产线名以便下拉显示
    station_options = [
        {"id": ws.id, "label": f"{ws.code} {ws.name}（{query.get_line(ws.line_id).name if query.get_line(ws.line_id) else ws.line_id}）"}
        for ws in stations
    ]
    return templates.TemplateResponse(
        request, "production/station.html",
        {"work_station_id": work_station_id, "station_options": station_options},
    )


@router.post("/production/station/load", response_class=HTMLResponse)
def station_load(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    try:
        view = StationService(db).load(scan, work_station_id, user.id)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_pass_result.html",
            {"error": e.detail, "work_station_id": work_station_id},
        )
    return templates.TemplateResponse(
        request, "production/station_view.html",
        {"view": view, "work_station_id": work_station_id},
    )


@router.post("/production/station/pass", response_class=HTMLResponse)
def station_pass(
    request: Request,
    work_station_id: int = Form(...),
    scan: str = Form(...),
    component_product_id: list[int] = Form(default=[]),
    component_batch: list[str] = Form(default=[]),
    param_key: list[str] = Form(default=[]),
    param_value: list[str] = Form(default=[]),
    param_unit: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    # 组件：仅收已扫批次的行
    components = [
        ComponentInput(component_product_id=pid, component_batch_no=batch.strip())
        for pid, batch in zip(component_product_id, component_batch)
        if batch.strip()
    ]
    # 参数：仅收 key+value 都非空的行
    params = []
    for i, key in enumerate(param_key):
        if not key.strip():
            continue
        val = param_value[i] if i < len(param_value) else ""
        if not val.strip():
            continue
        unit = param_unit[i].strip() if i < len(param_unit) and param_unit[i].strip() else None
        params.append(ParamInput(param_key=key.strip(), param_value=val.strip(), unit=unit))

    svc = OperationPassService(db)
    data = OperationPassInput(
        work_station_id=work_station_id, operator_id=user.id,
        components=components, params=params)
    # 先按 SN 试，NotFound 再当工单号（首件）
    try:
        data.sn = scan
        try:
            result = svc.pass_operation(data)
        except NotFoundError:
            data.sn = None
            data.work_order_code = scan
            result = svc.pass_operation(data)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_pass_result.html",
            {"error": e.detail, "work_station_id": work_station_id},
        )
    su = SerialUnitRepository(db).get_by_sn(result.sn)
    wo_id = su.work_order_id if su is not None else None
    # 成功分流：finished → 完工片段；next_op 可在本站继续 → 刷富界面到下一工序；否则切站提示
    if result.is_finished:
        return templates.TemplateResponse(
            request, "production/partials/station_pass_result.html",
            {"result": result, "work_station_id": work_station_id, "work_order_id": wo_id},
        )
    if result.next_op_can_continue_here and su is not None:
        # 调 load 组装下一工序富界面（scan=SN，因 SN 一定能 get_by_sn 命中）
        try:
            view = StationService(db).load(su.sn, work_station_id, user.id)
        except DomainError as e:
            db.rollback()
            return templates.TemplateResponse(
                request, "production/partials/station_pass_result.html",
                {"error": e.detail, "work_station_id": work_station_id},
            )
        return templates.TemplateResponse(
            request, "production/station_view.html",
            {"view": view, "work_station_id": work_station_id,
             "just_passed": result.passed_op},
        )
    # 下一工序不在本站 → 切站提示
    return templates.TemplateResponse(
        request, "production/partials/station_pass_result.html",
        {"result": result, "work_station_id": work_station_id, "work_order_id": wo_id,
         "switch_station": True},
    )


@router.get("/production/station/work-orders", response_class=HTMLResponse)
def station_work_orders(
    request: Request, work_station_id: int = Query(...), db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    ws = MasterDataQueryService(db).get_work_station(work_station_id)
    if ws is None:
        return HTMLResponse("")  # 作业站不存在 → 空片段
    wo_repo = ProductionService(db).work_orders
    su_repo = SerialUnitRepository(db)
    wo_list = [
        {"id": w.id, "code": w.code, "remaining": su_repo.count_pending_by_work_order(w.id)}
        for w in wo_repo.selectable_for_station(ws.line_id)
    ]
    return templates.TemplateResponse(
        request, "production/partials/station_wo_options.html",
        {"wo_list": wo_list},
    )


@router.post("/production/station/enter", response_class=HTMLResponse)
def station_enter(
    request: Request,
    work_station_id: int = Form(...),
    work_order_id: int = Form(...),
    scan: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    su_repo = SerialUnitRepository(db)
    load_svc = StationService(db)
    try:
        # I-1: 服务端校验工单与作业站产线一致（防篡改/下拉 bug 跨产线绑 SN）
        ws = MasterDataQueryService(db).get_work_station(work_station_id)
        wo = ProductionService(db).work_orders.get(work_order_id)
        if (ws is None or wo is None
                or wo.line_id != ws.line_id
                or wo.status not in ("released", "in_process")):
            raise BusinessRuleError("工单不可投产（需已下达且属本产线）")
        # 三路判定：SN → 活跃载体码（已过首工序的单元） → 首站新载体码（绑 SN）
        scan = scan.strip()
        su = su_repo.get_by_sn(scan)
        if su is None:
            bound = su_repo.get_active_by_carrier(scan)
            if bound is not None and bound.status != "pending":
                # 已过首工序的活跃载体码 → 加载跟踪（扫后续站载体码）
                su = bound
        if su is None:
            # 首站新载体码：绑 SN（不过站）。bind_first_carrier 内部校验
            # （重复扫已绑 pending 载体码 → "已绑定其他产品，请先解绑" 拦截）
            su = CarrierService(db).bind_first_carrier(work_order_id, scan, user.id)
        view = load_svc.load(su.sn, work_station_id, user.id)
    except DomainError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "production/partials/station_enter_error.html",
            {"error": e.detail, "work_station_id": work_station_id},
        )
    return templates.TemplateResponse(
        request, "production/station_view.html",
        {"view": view, "work_station_id": work_station_id},
    )
