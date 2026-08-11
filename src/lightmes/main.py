from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from lightmes.config import get_settings
from lightmes.database import get_db
from lightmes.modules import auth, integration, masterdata, production, trace, quality
from lightmes.database import engine
from lightmes.shared.base import Base
from lightmes.modules.auth.dependencies import current_user_or_none
from lightmes.modules.auth.models import User
from lightmes.shared.errors import DomainError

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
auth.register(app)
masterdata.register(app)
production.register(app)
trace.register(app)
integration.register(app)
quality.register(app)


@app.exception_handler(DomainError)
def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # 首页要求登录：未登录跳 /login，避免"看到首页但点功能被踢回登录"
    user = current_user_or_none(request, db)
    if user is None:
        return Response(status_code=302, headers={"Location": "/login"})
    return _templates.TemplateResponse(request, "home.html", {"user": user})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.on_event("startup")
def on_startup():
    """应用启动时初始化数据库和默认数据"""
    # 创建所有表（如果不存在）
    Base.metadata.create_all(bind=engine)

    # 初始化默认角色和管理员用户
    from lightmes.database import SessionLocal
    from lightmes.modules.auth.service import AuthService
    from lightmes.modules.production.defect_service import DefectService
    db = SessionLocal()
    try:
        AuthService(db).ensure_admin_user()
        DefectService(db).ensure_system_defect_types()
        db.commit()
    finally:
        db.close()
