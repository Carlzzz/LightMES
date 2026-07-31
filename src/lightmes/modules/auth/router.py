from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService

router = APIRouter()
templates = Jinja2Templates(directory="src/lightmes/templates")


@router.post("/api/auth/login")
def api_login(
    response: Response,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    user = AuthService(db).authenticate(username, password)
    if user is None:
        return JSONResponse(
            {"detail": "用户名或密码错误"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.session["user_id"] = user.id
    return JSONResponse({"username": user.username, "display_name": user.display_name})


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
        return HTMLResponse('<span style="color:red">用户名或密码错误</span>')
    request.session["user_id"] = user.id
    return HTMLResponse(f'<span style="color:green">欢迎，{user.display_name}</span>')
