
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, html_role_guard, require_role
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.models import Operation, WorkStation
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import (
    DefectRecord, DefectType,
    FirstInspectionConfig, FirstInspectionCheckItem,
    TestDataTemplate, TestDataField,
    WorkOrder,
)
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.production.quality_service import (
    FirstInspectionService, TestDataService,
)
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import (
    FirstInspectionConfigCreate, FirstInspectionCheckItemCreate,
    TestDataTemplateCreate, TestDataFieldCreate,
)
from lightmes.shared.errors import DomainError
from sqlalchemy.exc import IntegrityError

# 缺陷类型字段允许集合（service 层校验）
VALID_SEVERITIES = {"minor", "major", "critical"}
VALID_CATEGORIES = {"外观", "尺寸", "功能", "其他", None}

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _login_guard(request: Request, db: Session) -> Response | None:
    """登录守卫：返回 None 表示通过，返回 Response 表示拒绝（401 跳登录）。"""
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return None


def _can_manage_defect_types(request: Request, db: Session) -> bool:
    """缺陷类型主数据修改权限：仅 supervisor/admin。"""
    user = current_user_or_none(request, db)
    if user is None:
        return False
    role_name = user.role_obj.name if user.role_obj else None
    return role_name in ("admin", "supervisor")


def _manage_guard(request: Request, db: Session) -> Response | None:
    """首检/测试数据模板和缺陷类型配置：仅 supervisor/admin 可写。"""
    _, response = html_role_guard(request, db, "admin", "supervisor")
    return response


# ========== First Inspection Routes ==========

@router.get("/quality/first-inspection", response_class=HTMLResponse)
def first_inspection_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    configs = db.execute(
        select(FirstInspectionConfig).order_by(FirstInspectionConfig.id)
    ).scalars().all()
    operations = db.execute(select(Operation)).scalars().all()
    workstations = db.execute(select(WorkStation)).scalars().all()
    op_map = {op.id: op for op in operations}
    ws_map = {ws.id: ws for ws in workstations}
    return templates.TemplateResponse(
        request, "quality/first_inspection_list.html",
        {"configs": configs, "operations": operations, "workstations": workstations,
         "op_map": op_map, "ws_map": ws_map}
    )


@router.post("/quality/first-inspection", response_class=HTMLResponse)
def first_inspection_create(
    request: Request,
    name: str = Form(...),
    operation_id: int = Form(...),
    work_station_id: str = Form(""),
    is_enabled: bool = Form(False),
    trigger_new_order: bool = Form(False),
    trigger_material_change: bool = Form(False),
    trigger_tooling_change: bool = Form(False),
    trigger_param_revision: bool = Form(False),
    trigger_abnormal_restart: bool = Form(False),
    trigger_shift_handover: bool = Form(False),
    trigger_cold_start: bool = Form(False),
    trigger_previous_failed: bool = Form(False),
    require_authorization: bool = Form(False),
    quarantine_on_fail: bool = Form(False),
    sample_size: int = Form(1),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r

    ws_id = int(work_station_id) if work_station_id and work_station_id.isdigit() else None

    try:
        svc = FirstInspectionService(db)
        config = svc.create_config(FirstInspectionConfigCreate(
            operation_id=operation_id,
            work_station_id=ws_id,
            name=name,
            is_enabled=is_enabled,
            trigger_new_order=trigger_new_order,
            trigger_material_change=trigger_material_change,
            trigger_tooling_change=trigger_tooling_change,
            trigger_param_revision=trigger_param_revision,
            trigger_abnormal_restart=trigger_abnormal_restart,
            trigger_shift_handover=trigger_shift_handover,
            trigger_cold_start=trigger_cold_start,
            trigger_previous_failed=trigger_previous_failed,
            require_authorization=require_authorization,
            quarantine_on_fail=quarantine_on_fail,
            sample_size=sample_size,
        ))
        db.commit()
    except ValueError as e:
        db.rollback()
        configs = db.execute(select(FirstInspectionConfig).order_by(FirstInspectionConfig.id)).scalars().all()
        operations = db.execute(select(Operation)).scalars().all()
        workstations = db.execute(select(WorkStation)).scalars().all()
        op_map = {op.id: op for op in operations}
        ws_map = {ws.id: ws for ws in workstations}
        return templates.TemplateResponse(
            request, "quality/first_inspection_list.html",
            {"configs": configs, "operations": operations, "workstations": workstations,
             "op_map": op_map, "ws_map": ws_map, "error": str(e)}
        )

    configs = db.execute(select(FirstInspectionConfig).order_by(FirstInspectionConfig.id)).scalars().all()
    operations = db.execute(select(Operation)).scalars().all()
    workstations = db.execute(select(WorkStation)).scalars().all()
    op_map = {op.id: op for op in operations}
    ws_map = {ws.id: ws for ws in workstations}
    return templates.TemplateResponse(
        request, "quality/first_inspection_list.html",
        {"configs": configs, "operations": operations, "workstations": workstations,
         "op_map": op_map, "ws_map": ws_map}
    )


@router.get("/quality/first-inspection/{config_id}", response_class=HTMLResponse)
def first_inspection_detail(request: Request, config_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = FirstInspectionService(db)
    config = svc.get_config(config_id)
    if not config:
        return Response(status_code=404)
    check_items = svc.list_check_items(config_id)
    operations = db.execute(select(Operation)).scalars().all()
    workstations = db.execute(select(WorkStation)).scalars().all()
    op_map = {op.id: op for op in operations}
    ws_map = {ws.id: ws for ws in workstations}
    return templates.TemplateResponse(
        request, "quality/first_inspection_detail.html",
        {"config": config, "check_items": check_items,
         "operations": operations, "workstations": workstations,
         "op_map": op_map, "ws_map": ws_map}
    )


@router.post("/quality/first-inspection/{config_id}/check-items", response_class=HTMLResponse)
def check_item_create(
    request: Request,
    config_id: int,
    seq: int = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    check_type: str = Form("boolean"),
    unit: str = Form(""),
    standard_value: str = Form(""),
    min_value: str = Form(""),
    max_value: str = Form(""),
    is_mandatory: bool = Form(True),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r

    try:
        check_item = FirstInspectionCheckItem(
            config_id=config_id,
            seq=seq,
            name=name,
            description=description if description else None,
            check_type=check_type,
            unit=unit if unit else None,
            standard_value=standard_value if standard_value else None,
            min_value=float(min_value) if min_value and min_value.strip() else None,
            max_value=float(max_value) if max_value and max_value.strip() else None,
            is_mandatory=is_mandatory,
        )
        db.add(check_item)
        db.commit()
        db.refresh(check_item)
    except ValueError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": str(e), "colspan": 9}
        )

    return templates.TemplateResponse(
        request, "quality/partials/check_item_row.html",
        {"item": check_item}
    )


@router.post("/quality/first-inspection/{config_id}/check-items/{item_id}/delete")
def check_item_delete(
    request: Request,
    config_id: int,
    item_id: int,
    db: Session = Depends(get_db),
) -> Response:
    if (r := _manage_guard(request, db)): return r

    item = db.get(FirstInspectionCheckItem, item_id)
    if item and item.config_id == config_id:
        db.delete(item)
        db.commit()

    return Response(status_code=303, headers={"Location": f"/quality/first-inspection/{config_id}"})


# ========== Test Data Routes ==========

@router.get("/quality/test-data", response_class=HTMLResponse)
def test_data_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    templates_list = db.execute(
        select(TestDataTemplate).order_by(TestDataTemplate.id)
    ).scalars().all()
    operations = db.execute(select(Operation)).scalars().all()
    workstations = db.execute(select(WorkStation)).scalars().all()
    op_map = {op.id: op for op in operations}
    ws_map = {ws.id: ws for ws in workstations}
    return templates.TemplateResponse(
        request, "quality/test_data_list.html",
        {"templates": templates_list, "operations": operations, "workstations": workstations,
         "op_map": op_map, "ws_map": ws_map}
    )


@router.post("/quality/test-data", response_class=HTMLResponse)
def test_data_create(
    request: Request,
    name: str = Form(...),
    operation_id: int = Form(...),
    work_station_id: str = Form(""),
    is_enabled: bool = Form(False),
    version: str = Form("1"),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r

    ws_id = int(work_station_id) if work_station_id and work_station_id.isdigit() else None

    try:
        svc = TestDataService(db)
        template = svc.create_template(TestDataTemplateCreate(
            operation_id=operation_id,
            work_station_id=ws_id,
            name=name,
            is_enabled=is_enabled,
            version=version,
            description=description if description else None,
        ))
        db.commit()
    except ValueError as e:
        db.rollback()
        templates_list = db.execute(select(TestDataTemplate).order_by(TestDataTemplate.id)).scalars().all()
        operations = db.execute(select(Operation)).scalars().all()
        workstations = db.execute(select(WorkStation)).scalars().all()
        op_map = {op.id: op for op in operations}
        ws_map = {ws.id: ws for ws in workstations}
        return templates.TemplateResponse(
            request, "quality/test_data_list.html",
            {"templates": templates_list, "operations": operations, "workstations": workstations,
             "op_map": op_map, "ws_map": ws_map, "error": str(e)}
        )

    templates_list = db.execute(select(TestDataTemplate).order_by(TestDataTemplate.id)).scalars().all()
    operations = db.execute(select(Operation)).scalars().all()
    workstations = db.execute(select(WorkStation)).scalars().all()
    op_map = {op.id: op for op in operations}
    ws_map = {ws.id: ws for ws in workstations}
    return templates.TemplateResponse(
        request, "quality/test_data_list.html",
        {"templates": templates_list, "operations": operations, "workstations": workstations,
         "op_map": op_map, "ws_map": ws_map}
    )


@router.get("/quality/test-data/{template_id}", response_class=HTMLResponse)
def test_data_detail(request: Request, template_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    svc = TestDataService(db)
    template = svc.get_template(template_id)
    if not template:
        return Response(status_code=404)
    fields = svc.list_fields(template_id)
    operations = db.execute(select(Operation)).scalars().all()
    workstations = db.execute(select(WorkStation)).scalars().all()
    op_map = {op.id: op for op in operations}
    ws_map = {ws.id: ws for ws in workstations}
    return templates.TemplateResponse(
        request, "quality/test_data_detail.html",
        {"template": template, "fields": fields,
         "operations": operations, "workstations": workstations,
         "op_map": op_map, "ws_map": ws_map}
    )


@router.post("/quality/test-data/{template_id}/fields", response_class=HTMLResponse)
def test_field_create(
    request: Request,
    template_id: int,
    seq: int = Form(...),
    code: str = Form(...),
    name: str = Form(...),
    field_type: str = Form("numeric"),
    unit: str = Form(""),
    is_required: bool = Form(True),
    standard_value: str = Form(""),
    min_value: str = Form(""),
    max_value: str = Form(""),
    display_group: str = Form(""),
    options: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r

    try:
        field = TestDataField(
            template_id=template_id,
            seq=seq,
            code=code,
            name=name,
            field_type=field_type,
            unit=unit if unit else None,
            is_required=is_required,
            standard_value=standard_value if standard_value else None,
            min_value=float(min_value) if min_value and min_value.strip() else None,
            max_value=float(max_value) if max_value and max_value.strip() else None,
            display_group=display_group if display_group else None,
            options=[opt.strip() for opt in options.split(",")] if options and options.strip() else None,
        )
        db.add(field)
        db.commit()
        db.refresh(field)
    except ValueError as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": str(e), "colspan": 12}
        )

    return templates.TemplateResponse(
        request, "quality/partials/test_field_row.html",
        {"field": field}
    )


@router.post("/quality/test-data/{template_id}/fields/{field_id}/delete")
def test_field_delete(
    request: Request,
    template_id: int,
    field_id: int,
    db: Session = Depends(get_db),
) -> Response:
    if (r := _manage_guard(request, db)): return r

    field = db.get(TestDataField, field_id)
    if field and field.template_id == template_id:
        db.delete(field)
        db.commit()

    return Response(status_code=303, headers={"Location": f"/quality/test-data/{template_id}"})


@router.post("/quality/first-inspection/{config_id}/delete")
def first_inspection_delete(
    request: Request,
    config_id: int,
    db: Session = Depends(get_db),
) -> Response:
    if (r := _manage_guard(request, db)): return r

    config = db.get(FirstInspectionConfig, config_id)
    if config:
        # Delete check items first
        check_items = db.execute(
            select(FirstInspectionCheckItem).where(FirstInspectionCheckItem.config_id == config_id)
        ).scalars().all()
        for item in check_items:
            db.delete(item)
        # Delete config
        db.delete(config)
        db.commit()

    return Response(status_code=303, headers={"Location": "/quality/first-inspection"})


@router.post("/quality/first-inspection/{config_id}/update")
def first_inspection_update(
    request: Request,
    config_id: int,
    name: str = Form(...),
    operation_id: int = Form(...),
    work_station_id: str = Form(""),
    is_enabled: bool = Form(False),
    trigger_new_order: bool = Form(False),
    trigger_material_change: bool = Form(False),
    trigger_tooling_change: bool = Form(False),
    trigger_param_revision: bool = Form(False),
    trigger_abnormal_restart: bool = Form(False),
    trigger_shift_handover: bool = Form(False),
    trigger_cold_start: bool = Form(False),
    trigger_previous_failed: bool = Form(False),
    require_authorization: bool = Form(False),
    quarantine_on_fail: bool = Form(False),
    sample_size: int = Form(1),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r

    ws_id = int(work_station_id) if work_station_id and work_station_id.isdigit() else None

    try:
        config = db.get(FirstInspectionConfig, config_id)
        if not config:
            return Response(status_code=404)

        # Update fields
        config.name = name
        config.operation_id = operation_id
        config.work_station_id = ws_id
        config.is_enabled = is_enabled
        config.trigger_new_order = trigger_new_order
        config.trigger_material_change = trigger_material_change
        config.trigger_tooling_change = trigger_tooling_change
        config.trigger_param_revision = trigger_param_revision
        config.trigger_abnormal_restart = trigger_abnormal_restart
        config.trigger_shift_handover = trigger_shift_handover
        config.trigger_cold_start = trigger_cold_start
        config.trigger_previous_failed = trigger_previous_failed
        config.require_authorization = require_authorization
        config.quarantine_on_fail = quarantine_on_fail
        config.sample_size = sample_size

        db.commit()
    except ValueError as e:
        db.rollback()

    return Response(status_code=303, headers={"Location": f"/quality/first-inspection/{config_id}"})


@router.post("/quality/test-data/{template_id}/delete")
def test_data_delete(
    request: Request,
    template_id: int,
    db: Session = Depends(get_db),
) -> Response:
    if (r := _manage_guard(request, db)): return r

    template = db.get(TestDataTemplate, template_id)
    if template:
        # Delete fields first
        fields = db.execute(
            select(TestDataField).where(TestDataField.template_id == template_id)
        ).scalars().all()
        for field in fields:
            db.delete(field)
        # Delete template
        db.delete(template)
        db.commit()

    return Response(status_code=303, headers={"Location": "/quality/test-data"})


@router.post("/quality/test-data/{template_id}/update")
def test_data_update(
    request: Request,
    template_id: int,
    name: str = Form(...),
    operation_id: int = Form(...),
    work_station_id: str = Form(""),
    is_enabled: bool = Form(False),
    version: str = Form("1"),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r

    ws_id = int(work_station_id) if work_station_id and work_station_id.isdigit() else None

    try:
        template = db.get(TestDataTemplate, template_id)
        if not template:
            return Response(status_code=404)

        # Update fields
        template.name = name
        template.operation_id = operation_id
        template.work_station_id = ws_id
        template.is_enabled = is_enabled
        template.version = version
        template.description = description if description else None

        db.commit()
    except ValueError as e:
        db.rollback()

    return Response(status_code=303, headers={"Location": f"/quality/test-data/{template_id}"})


# ========== Defect Type Routes ==========

@router.get("/quality/defect-types", response_class=HTMLResponse)
def defect_types_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    types = db.execute(
        select(DefectType).order_by(DefectType.id)
    ).scalars().all()
    return templates.TemplateResponse(
        request, "quality/defect_types.html",
        {"types": types})


@router.post("/quality/defect-types", response_class=HTMLResponse)
def defect_type_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    category: str = Form(""),
    severity: str = Form("major"),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _manage_guard(request, db)): return r
    # 字段白名单校验
    cat = category if category else None
    if severity not in VALID_SEVERITIES:
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": f"严重度必须是 {sorted(VALID_SEVERITIES)} 之一", "colspan": 6})
    if cat not in VALID_CATEGORIES:
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": f"分类必须是 {sorted(c for c in VALID_CATEGORIES if c)} 之一", "colspan": 6})
    try:
        dt = DefectType(
            code=code, name=name, category=cat,
            severity=severity,
            description=description if description else None)
        db.add(dt); db.commit(); db.refresh(dt)
        return templates.TemplateResponse(
            request, "quality/partials/defect_type_row.html",
            {"dt": dt})
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": f"编码已存在: {code}", "colspan": 6})
    except Exception:
        db.rollback()
        return templates.TemplateResponse(
            request, "quality/partials/error_row.html",
            {"error": "创建失败，请检查输入", "colspan": 6})


@router.post("/quality/defect-types/{dt_id}/delete")
def defect_type_delete(
    request: Request, dt_id: int, db: Session = Depends(get_db),
) -> Response:
    if (r := _manage_guard(request, db)): return r
    dt = db.get(DefectType, dt_id)
    if dt:
        dt.is_active = False  # 软删
        db.commit()
    return Response(status_code=303, headers={"Location": "/quality/defect-types"})


# ========== Defect Logging Routes ==========

@router.get("/quality/defects/log", response_class=HTMLResponse)
def defect_log_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    sn = request.query_params.get("sn", "")
    types = db.execute(
        select(DefectType).where(DefectType.is_active == True).order_by(DefectType.code)
    ).scalars().all()
    return templates.TemplateResponse(
        request, "quality/defect_log.html",
        {"types": types, "sn": sn})


@router.post("/quality/defects/log", response_class=HTMLResponse)
def defect_log_submit(
    request: Request,
    sn: str = Form(...),
    defect_type_id: int = Form(...),
    position: str = Form(""),
    remark: str = Form(""),
    create_issue: bool = Form(False),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    user = current_user_or_none(request, db)
    try:
        record = DefectService(db).log_defect(
            defect_type_id=defect_type_id, sn=sn, discovered_by=user.id,
            position=position if position else None,
            remark=remark if remark else None,
            create_issue=create_issue)
        db.commit()
    except DomainError as e:
        # 领域错误（SN 不存在/已隔离等）：用户可见的中文消息
        db.rollback()
        types = db.execute(select(DefectType).where(DefectType.is_active == True).order_by(DefectType.code)).scalars().all()
        return templates.TemplateResponse(
            request, "quality/defect_log.html",
            {"types": types, "sn": sn, "error": e.detail})
    except Exception:
        # 未预期错误：不暴露内部细节
        db.rollback()
        types = db.execute(select(DefectType).where(DefectType.is_active == True).order_by(DefectType.code)).scalars().all()
        return templates.TemplateResponse(
            request, "quality/defect_log.html",
            {"types": types, "sn": sn, "error": "登记失败，请稍后重试或联系管理员"})
    return templates.TemplateResponse(
        request, "quality/partials/defect_log_success.html",
        {"record": record})


# ========== Defect List / Detail / Handling Routes ==========

@router.get("/quality/defects", response_class=HTMLResponse)
def defect_list_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    status_filter = request.query_params.get("status", "")
    q = select(DefectRecord).order_by(DefectRecord.discovered_at.desc())
    if status_filter:
        q = q.where(DefectRecord.handling_status == status_filter)
    records = db.execute(q).scalars().all()
    return templates.TemplateResponse(
        request, "quality/defect_list.html",
        {"records": records, "status_filter": status_filter})


@router.get("/quality/defects/{record_id}", response_class=HTMLResponse)
def defect_detail_page(request: Request, record_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    record = db.get(DefectRecord, record_id)
    if record is None:
        return Response(status_code=404)
    su = SerialUnitRepository(db).get(record.serial_unit_id)
    wo = db.get(WorkOrder, record.work_order_id)
    # 工序列表（用于返工 target_seq 下拉）
    operations = MasterDataQueryService(db).get_operations(wo.routing_id) if wo else []
    user = current_user_or_none(request, db)
    can_handle = user is not None and user.role_obj is not None and user.role_obj.name in ("admin", "supervisor")
    return templates.TemplateResponse(
        request, "quality/defect_detail.html",
        {"record": record, "su": su, "operations": operations, "can_handle": can_handle})


@router.get("/quality/defects/{record_id}/rework-stations", response_class=HTMLResponse)
def defect_rework_stations(
    request: Request, record_id: int,
    target_seq: int = Query(...), db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX：target_seq 选定后联动站位下拉（复用 P2h _resolve_rework_stations 模式）。"""
    if (r := _login_guard(request, db)): return r
    record = db.get(DefectRecord, record_id)
    if record is None:
        return Response(status_code=404)
    su = SerialUnitRepository(db).get(record.serial_unit_id)
    wo = db.get(WorkOrder, su.work_order_id)
    query = MasterDataQueryService(db)
    operations = query.get_operations(wo.routing_id)
    first_repass_op = next((o for o in operations if o.seq > target_seq), None)
    stations = []
    if first_repass_op:
        allowed = query.get_allowed_work_stations(first_repass_op.id)
        station_ids = [w.id for w in allowed] or [first_repass_op.default_work_station_id]
        stations = list(db.execute(
            select(WorkStation).where(WorkStation.id.in_(station_ids))
        ).scalars().all())
    return templates.TemplateResponse(
        request, "quality/partials/rework_stations.html",
        {"stations": stations, "first_repass_op": first_repass_op})


@router.post("/quality/defects/{record_id}/handle-rework", response_class=HTMLResponse)
def defect_handle_rework(
    request: Request, record_id: int,
    target_seq: int = Form(...),
    expected_repass_station_id: int = Form(...),
    remark: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    try:
        DefectService(db).handle_rework(
            record_id=record_id, handled_by=user.id,
            target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
            remark=remark or None)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=422, content=e.detail)
    except Exception:
        db.rollback()
        return Response(status_code=422, content="处理失败，请稍后重试")
    return Response(status_code=303, headers={"Location": f"/quality/defects/{record_id}"})


@router.post("/quality/defects/{record_id}/handle-scrap", response_class=HTMLResponse)
def defect_handle_scrap(
    request: Request, record_id: int,
    remark: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    try:
        DefectService(db).handle_scrap(
            record_id=record_id, handled_by=user.id, remark=remark or None)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=422, content=e.detail)
    except Exception:
        db.rollback()
        return Response(status_code=422, content="处理失败，请稍后重试")
    return Response(status_code=303, headers={"Location": f"/quality/defects/{record_id}"})


@router.post("/quality/defects/{record_id}/handle-concession", response_class=HTMLResponse)
def defect_handle_concession(
    request: Request, record_id: int,
    remark: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "supervisor")),
) -> HTMLResponse:
    try:
        DefectService(db).handle_concession(
            record_id=record_id, handled_by=user.id, remark=remark or None)
        db.commit()
    except DomainError as e:
        db.rollback()
        return Response(status_code=422, content=e.detail)
    except Exception:
        db.rollback()
        return Response(status_code=422, content="处理失败，请稍后重试")
    return Response(status_code=303, headers={"Location": f"/quality/defects/{record_id}"})


