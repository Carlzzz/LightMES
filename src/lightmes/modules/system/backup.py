import os

from fastapi import APIRouter, Depends, HTTPException, status

from lightmes.config import get_settings
from lightmes.modules.auth.dependencies import require_role
from lightmes.modules.auth.models import User

router = APIRouter()


@router.get("/api/system/settings/export")
def export_settings(_: User = Depends(require_role("admin"))) -> dict:
    """Return a minimal, non-secret view of the current application settings."""
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "max_import_bytes": settings.max_import_bytes,
        "max_import_rows": settings.max_import_rows,
        "session_max_age_seconds": settings.session_max_age_seconds,
        "login_rate_limit": settings.login_rate_limit,
        "api_rate_limit": settings.api_rate_limit,
        "rate_limit_window_seconds": settings.rate_limit_window_seconds,
    }


@router.get("/api/system/db-dump")
def db_dump(_: User = Depends(require_role("admin"))) -> dict:
    """Placeholder for a safe DB dump. Disabled unless explicitly opted in."""
    if os.getenv("ENABLE_DB_DUMP_API", "").strip().lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Database dump API is disabled",
        )
    return {"status": "disabled"}
