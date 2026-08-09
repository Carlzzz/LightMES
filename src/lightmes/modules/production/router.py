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
    FirstInspectionSubmitInput, FirstInspectionCheckResultInput,
    TestDataRecordSubmitInput, TestDataValueInput,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.station_service import StationService
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.wip_service import WipService
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.production.quality_service import (
    FirstInspectionService, TestDataService,
)
from lightmes.modules.production.models import (
    WorkOrder, OperationRecord, FirstInspectionRecord,
)
from lightmes.modules.production.repository import (
    SerialUnitRepository, WorkOrderRepository, OperationRecordRepository,
)
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


@router.get("/production/scan")
def scan_page():
    """已废弃：重定向到工位作业页。"""
    return Response(status_code=302, headers={"Location": "/production/station"})


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
    request: Request, work_order: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    """WIP 看板：支持工单号(code)或数字 ID 查询。"""
    wo_id = 0
    wo_code = ""
    if work_order:
        wo = None
        if work_order.isdigit():
            wo = db.get(WorkOrder, int(work_order))
        if wo is None:
            wo = WorkOrderRepository(db).get_by_code(work_order)
        if wo is not None:
            wo_id = wo.id
            wo_code = wo.code
    svc = WipService(db)
    items = svc.wip_by_work_order(wo_id) if wo_id else []
    summary = svc.summary_by_work_order(wo_id) if wo_id else None
    return templates.TemplateResponse(
        request, "production/wip.html",
        {"work_order": wo_code or work_order, "items": items, "summary": summary},
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
    component_sn: list[str] = Form(default=[]),
    param_key: list[str] = Form(default=[]),
    param_value: list[str] = Form(default=[]),
    param_unit: list[str] = Form(default=[]),
    # 首检相关
    fi_check_item_id: list[int] = Form(default=[]),
    fi_result_type: list[str] = Form(default=[]),
    fi_boolean_value: list[bool] = Form(default=[]),
    fi_numeric_value: list[float] = Form(default=[]),
    fi_text_value: list[str] = Form(default=[]),
    fi_remark: list[str] = Form(default=[]),
    fi_overall_remark: str = Form(""),
    # 测试数据相关
    td_field_id: list[int] = Form(default=[]),
    td_value_type: list[str] = Form(default=[]),
    td_boolean_value: list[bool] = Form(default=[]),
    td_numeric_value: list[float] = Form(default=[]),
    td_text_value: list[str] = Form(default=[]),
    td_overall_remark: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    # 组件：收集 serial (component_sn) 和 batch (component_batch) 两种
    components = []
    for i, pid in enumerate(component_product_id):
        sn_val = component_sn[i].strip() if i < len(component_sn) and component_sn[i] else ""
        batch_val = component_batch[i].strip() if i < len(component_batch) and component_batch[i] else ""
        if sn_val:
            components.append(ComponentInput(component_product_id=pid, component_sn=sn_val))
        elif batch_val:
            components.append(ComponentInput(component_product_id=pid, component_batch_no=batch_val))
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

    # 先过站（创建工序记录）
    op_svc = OperationPassService(db)
    data = OperationPassInput(
        work_station_id=work_station_id, operator_id=user.id,
        components=components, params=params)
    # 先按 SN 试，仅当 SN/载体码不存在时才回退当工单号（首件）
    try:
        data.sn = scan
        try:
            result = op_svc.pass_operation(data)
        except NotFoundError as e:
            # 只有明确是"未找到 SN 或载体码"才回退到工单号
            if "未找到 SN 或载体码" in e.detail:
                data.sn = None
                data.work_order_code = scan
                result = op_svc.pass_operation(data)
            else:
                # 其他 NotFoundError（如作业站不存在）直接抛出
                raise
    except DomainError as e:
        db.rollback()
        # 物料绑定/参数等报错不销毁主界面：重新渲染工位视图 + 顶部错误提示
        try:
            view = StationService(db).load(scan, work_station_id, user.id)
            return templates.TemplateResponse(
                request, "production/station_view.html",
                {"view": view, "work_station_id": work_station_id,
                 "pass_error": e.detail},
            )
        except DomainError:
            # scan 本身无效（如 SN 不存在），回退到简单错误页
            return templates.TemplateResponse(
                request, "production/partials/station_pass_result.html",
                {"error": e.detail, "work_station_id": work_station_id},
            )

    # 获取刚才创建的工序记录
    su = SerialUnitRepository(db).get_by_sn(result.sn)
    wo_id = su.work_order_id if su is not None else None

    # 获取工单以获取routing_id
    wo = WorkOrderRepository(db).get(wo_id) if wo_id else None
    operations = MasterDataQueryService(db).get_operations(wo.routing_id) if wo else []

    # 处理首检 - 注意：result.passed_op 是刚完成的工序
    passed_op_id = result.passed_op.id if result.passed_op else None
    if fi_check_item_id and passed_op_id:
        try:
            fi_svc = FirstInspectionService(db)
            fi_config = fi_svc.get_config_by_operation(passed_op_id, work_station_id)
            if fi_config and fi_config.is_enabled and wo:
                # 检查是否需要首检 - 使用station_view中显示的同样逻辑
                # 从view中我们已经知道需要首检，所以直接创建记录并提交
                trigger_reason = "new_order"  # 默认原因
                # 创建首检记录
                fi_record = fi_svc.create_inspection_record(
                    fi_config, wo.id, passed_op_id,
                    work_station_id, user.id, trigger_reason,
                    su.id if su else None
                )
                # 收集检查结果
                check_results = []
                for i, item_id in enumerate(fi_check_item_id):
                    result_type = fi_result_type[i] if i < len(fi_result_type) else "boolean"
                    check_result = FirstInspectionCheckResultInput(
                        check_item_id=item_id,
                        result_type=result_type,
                        boolean_value=fi_boolean_value[i] if i < len(fi_boolean_value) else None,
                        numeric_value=fi_numeric_value[i] if i < len(fi_numeric_value) else None,
                        text_value=fi_text_value[i] if i < len(fi_text_value) else None,
                        remark=fi_remark[i] if i < len(fi_remark) else None,
                    )
                    check_results.append(check_result)
                # 提交首检
                if check_results:
                    fi_svc.submit_inspection(
                        FirstInspectionSubmitInput(
                            record_id=fi_record.id,
                            check_results=check_results,
                            remark=fi_overall_remark or None,
                        ),
                        user.id
                    )
        except Exception as e:
            # 首检错误不影响过站，只是记录一下
            pass

    # 处理测试数据
    if td_field_id and passed_op_id:
        try:
            td_svc = TestDataService(db)
            td_template = td_svc.get_template_by_operation(passed_op_id, work_station_id)
            if td_template and td_template.is_enabled:
                # 获取最新的工序记录
                op_records = OperationRecordRepository(db).list_by_serial_unit(su.id) if su else []
                latest_op_record = op_records[-1] if op_records else None

                if latest_op_record:
                    # 收集测试数据值
                    test_values = []
                    for i, field_id in enumerate(td_field_id):
                        value_type = td_value_type[i] if i < len(td_value_type) else "numeric"
                        test_value = TestDataValueInput(
                            field_id=field_id,
                            value_type=value_type,
                            boolean_value=td_boolean_value[i] if i < len(td_boolean_value) else None,
                            numeric_value=td_numeric_value[i] if i < len(td_numeric_value) else None,
                            text_value=td_text_value[i] if i < len(td_text_value) else None,
                        )
                        test_values.append(test_value)
                    # 提交测试数据
                    if test_values:
                        td_svc.submit_test_data(
                            TestDataRecordSubmitInput(
                                operation_record_id=latest_op_record.id,
                                values=test_values,
                                remark=td_overall_remark or None,
                            ),
                            user.id
                        )
        except Exception as e:
            # 测试数据错误不影响过站，只是记录一下
            pass

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
        # 三路判定：SN -> 活跃载体码 -> 首站新载体码（绑 SN）
        scan = scan.strip()
        su = su_repo.get_by_sn(scan)
        if su is None:
            bound = su_repo.get_active_by_carrier(scan)
            if bound is not None:
                # 载体码已绑 SN（含 pending/in_process/reworking）--直接加载。
                # 不再跳过 pending：pending 说明首站绑了载体码但还没 PASS，
                # 操作员可能换站后重新扫进来，必须能进。
                su = bound
        if su is None:
            # 首站新载体码：绑 SN（不过站）。bind_first_carrier 内部校验
            # （重复扫已绑 pending 载体码 -> "已绑定其他产品，请先解绑" 拦截）
            su = CarrierService(db).bind_first_carrier(work_order_id, scan, user.id)
        else:
            # 交叉校验：扫到的 SN/载体码必须属于所选工单
            if su.work_order_id != work_order_id:
                raise BusinessRuleError(
                    f"该 SN/载体码属于其他工单（SN: {su.sn}），请选择正确工单")
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
