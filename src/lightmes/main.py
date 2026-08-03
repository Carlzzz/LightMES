from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from lightmes.config import get_settings
from lightmes.modules import auth, masterdata, production
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


@app.exception_handler(DomainError)
def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "home.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
