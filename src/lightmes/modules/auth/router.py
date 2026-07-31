from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.schemas import LoginResponse
from lightmes.modules.auth.service import AuthService

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


@router.post("/api/auth/login", response_model=LoginResponse)
def api_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> LoginResponse:
    user = AuthService(db).authenticate(username, password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    request.session["user_id"] = user.id
    return LoginResponse(username=user.username, display_name=user.display_name)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html")


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = AuthService(db).authenticate(username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "partials/login_result.html", {"user": None}
        )
    request.session["user_id"] = user.id
    return templates.TemplateResponse(
        request, "partials/login_result.html", {"user": user}
    )
