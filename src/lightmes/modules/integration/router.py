from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.config import get_settings
from lightmes.modules.auth.dependencies import current_user_or_none, html_role_guard, require_role
from lightmes.modules.auth.models import User
from lightmes.modules.integration.schemas import SyncResult
from lightmes.modules.integration.service import FileErpSyncService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


async def _read_limited(file: UploadFile) -> bytes:
    max_bytes = get_settings().max_import_bytes
    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"上传文件超过 {max_bytes} 字节限制",
        )
    return raw


@router.post("/api/integration/import/products", response_model=SyncResult)
async def api_import_products(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "supervisor")),
) -> SyncResult:
    raw = await _read_limited(file)
    return FileErpSyncService(db).sync_products(raw)


@router.post("/api/integration/import/boms", response_model=SyncResult)
async def api_import_boms(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "supervisor")),
) -> SyncResult:
    raw = await _read_limited(file)
    return FileErpSyncService(db).sync_boms(raw)


@router.get("/integration/import", response_class=HTMLResponse)
def import_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "integration/import.html")


@router.post("/integration/import", response_class=HTMLResponse)
async def import_submit(
    request: Request,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _, auth_response = html_role_guard(request, db, "admin", "supervisor")
    if auth_response is not None:
        return auth_response
    try:
        raw = await _read_limited(file)
    except HTTPException as e:
        return templates.TemplateResponse(
            request,
            "integration/partials/sync_result.html",
            {"result": {"created": 0, "updated": 0, "skipped": 1, "errors": [e.detail]}},
        )
    svc = FileErpSyncService(db)
    result = svc.sync_products(raw) if kind == "products" else svc.sync_boms(raw)
    return templates.TemplateResponse(
        request, "integration/partials/sync_result.html", {"result": result}
    )
