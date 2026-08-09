
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.models import Operation, WorkStation
from lightmes.modules.production.models import (
    FirstInspectionConfig, FirstInspectionCheckItem,
    TestDataTemplate, TestDataField,
)
from lightmes.modules.production.quality_service import (
    FirstInspectionService, TestDataService,
)
from lightmes.modules.production.schemas import (
    FirstInspectionConfigCreate, FirstInspectionCheckItemCreate,
    TestDataTemplateCreate, TestDataFieldCreate,
)

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _login_guard(request: Request, db: Session) -> Response | None:
    if current_user_or_none(request, db) is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return None


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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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
    if (r := _login_guard(request, db)): return r

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

