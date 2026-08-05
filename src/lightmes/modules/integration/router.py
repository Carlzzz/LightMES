from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.dependencies import current_user_or_none, require_login
from lightmes.modules.auth.models import User
from lightmes.modules.integration.schemas import SyncResult
from lightmes.modules.integration.service import FileErpSyncService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.post("/api/integration/import/products", response_model=SyncResult)
async def api_import_products(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> SyncResult:
    raw = await file.read()
    return FileErpSyncService(db).sync_products(raw)


@router.post("/api/integration/import/boms", response_model=SyncResult)
async def api_import_boms(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
) -> SyncResult:
    raw = await file.read()
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
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    raw = await file.read()
    svc = FileErpSyncService(db)
    result = svc.sync_products(raw) if kind == "products" else svc.sync_boms(raw)
    return templates.TemplateResponse(
        request, "integration/partials/sync_result.html", {"result": result}
    )
