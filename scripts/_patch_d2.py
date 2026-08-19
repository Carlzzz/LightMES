"""D2 后端批量补丁（一次性）。"""
import io


def patch(path, pairs):
    with io.open(path, encoding="utf-8") as f:
        src = f.read()
    for old, new in pairs:
        if old not in src:
            raise SystemExit(f"NOT FOUND in {path}:\n{old[:100]}")
        src = src.replace(old, new, 1)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("patched", path)


BASE = r"C:\Users\zhaocao\Documents\GitHub\LightMES\src\lightmes"

# 1) masterdata schemas
patch(BASE + r"\modules\masterdata\schemas.py", [
    ("""class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int]  # 新增：至少 1 个；必须含 default_work_station_id
    is_mandatory: bool = True
    required_skill_id: int | None = None
    required_level: int | None = None""",
     """class OperationCreate(BaseModel):
    seq: int
    code: str
    name: str
    default_work_station_id: int
    allowed_work_station_ids: list[int]  # 新增：至少 1 个；必须含 default_work_station_id
    is_mandatory: bool = True
    require_material_binding: bool = False
    require_param_collection: bool = False
    required_skill_id: int | None = None
    required_level: int | None = None"""),
    ("""    default_work_station_id: int
    allowed_work_station_ids: list[int] = []
    is_mandatory: bool""",
     """    default_work_station_id: int
    allowed_work_station_ids: list[int] = []
    is_mandatory: bool
    require_material_binding: bool = False
    require_param_collection: bool = False"""),
])

# 2) production schemas StationView
patch(BASE + r"\modules\production\schemas.py", [
    ("""    first_inspection: FirstInspectionStationView | None = None
    test_data: TestDataStationView | None = None
    blocking_issue: Any | None = None  # Issue 模型或None""",
     """    first_inspection: FirstInspectionStationView | None = None
    test_data: TestDataStationView | None = None
    blocking_issue: Any | None = None  # Issue 模型或None
    require_material_binding: bool = False
    require_param_collection: bool = False"""),
])

# 3) masterdata service
patch(BASE + r"\modules\masterdata\service.py", [
    ("""    def update_operation(self, operation_id: int, *, seq, code, name,
                         default_work_station_id, allowed_work_station_ids,
                         required_skill_id, required_level, is_mandatory=True) -> Operation:""",
     """    def update_operation(self, operation_id: int, *, seq, code, name,
                         default_work_station_id, allowed_work_station_ids,
                         required_skill_id, required_level, is_mandatory=True,
                         require_material_binding=False, require_param_collection=False) -> Operation:"""),
    ("""        op.is_mandatory = is_mandatory
        op.required_skill_id = required_skill_id
        op.required_level = required_level""",
     """        op.is_mandatory = is_mandatory
        op.require_material_binding = require_material_binding
        op.require_param_collection = require_param_collection
        op.required_skill_id = required_skill_id
        op.required_level = required_level"""),
    ("""    def add_operation(self, routing_id: int, *, seq, code, name,
                      default_work_station_id, allowed_work_station_ids,
                      required_skill_id, required_level, is_mandatory=True) -> Operation:""",
     """    def add_operation(self, routing_id: int, *, seq, code, name,
                      default_work_station_id, allowed_work_station_ids,
                      required_skill_id, required_level, is_mandatory=True,
                      require_material_binding=False, require_param_collection=False) -> Operation:"""),
    ("""        op = Operation(routing_id=routing_id, seq=seq, code=code, name=name,
                       default_work_station_id=default_work_station_id,
                       is_mandatory=is_mandatory,
                       required_skill_id=required_skill_id,
                       required_level=required_level)""",
     """        op = Operation(routing_id=routing_id, seq=seq, code=code, name=name,
                       default_work_station_id=default_work_station_id,
                       is_mandatory=is_mandatory,
                       require_material_binding=require_material_binding,
                       require_param_collection=require_param_collection,
                       required_skill_id=required_skill_id,
                       required_level=required_level)"""),
])

# 4) page_router
patch(BASE + r"\modules\masterdata\page_router.py", [
    ("""    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
    op_ws: int = Form(...), op_allowed: str = Form(""),
    op_skill: str = Form(""), op_level: str = Form(""),
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
            is_mandatory=True)""",
     """    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
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
            require_param_collection=op_req_param)"""),
    ("""    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
    op_ws: int = Form(...), op_allowed: str = Form(""),
    op_skill: str = Form(""), op_level: str = Form(""),
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
            is_mandatory=True)""",
     """    seq: int = Form(...), code: str = Form(...), name: str = Form(...),
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
            require_param_collection=op_req_param)"""),
])

# 5) process_snapshot
patch(BASE + r"\modules\production\process_snapshot.py", [
    ("""    required_skill_id: int | None
    required_level: int | None
    sop_text: str | None
    sop_url: str | None""",
     """    required_skill_id: int | None
    required_level: int | None
    require_material_binding: bool = False
    require_param_collection: bool = False
    sop_text: str | None = None
    sop_url: str | None = None"""),
    ("""                "required_skill_id": op.required_skill_id,
                "required_level": op.required_level,
                "sop_text": op.sop_text,
                "sop_url": op.sop_url,""",
     """                "required_skill_id": op.required_skill_id,
                "required_level": op.required_level,
                "require_material_binding": bool(getattr(op, "require_material_binding", False)),
                "require_param_collection": bool(getattr(op, "require_param_collection", False)),
                "sop_text": op.sop_text,
                "sop_url": op.sop_url,"""),
    ("""            required_skill_id=op.get("required_skill_id"),
            required_level=op.get("required_level"),
            sop_text=op.get("sop_text"),
            sop_url=op.get("sop_url")""",
     """            required_skill_id=op.get("required_skill_id"),
            required_level=op.get("required_level"),
            require_material_binding=bool(op.get("require_material_binding", False)),
            require_param_collection=bool(op.get("require_param_collection", False)),
            sop_text=op.get("sop_text"),
            sop_url=op.get("sop_url")"""),
    ("""            required_skill_id=op.required_skill_id,
            required_level=op.required_level,
            sop_text=op.sop_text,
            sop_url=op.sop_url,
        )
        for op in query.get_operations(work_order.routing_id)""",
     """            required_skill_id=op.required_skill_id,
            required_level=op.required_level,
            require_material_binding=bool(getattr(op, "require_material_binding", False)),
            require_param_collection=bool(getattr(op, "require_param_collection", False)),
            sop_text=op.sop_text,
            sop_url=op.sop_url,
        )
        for op in query.get_operations(work_order.routing_id)"""),
])

print("ALL BACKEND PATCHES OK")
