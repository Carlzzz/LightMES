from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.auth.models import User, Role
from lightmes.modules.auth.schemas import (
    LoginResponse, UserCreate, UserUpdate, RoleCreate, RoleUpdate,
    UserRead, RoleRead,
)
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.dependencies import (
    require_login, require_permission, current_user_or_none,
)
from lightmes.modules.auth.repository import UserRepository, RoleRepository

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _login_guard(request: Request, db: Session) -> Response | None:
    if current_user_or_none(request, db) is None:
        return Response(status_code=302, headers={"Location": "/login"})
    return None


@router.post("/api/auth/login", response_model=LoginResponse)
def api_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> LoginResponse:
    auth_service = AuthService(db)
    # 确保默认角色和管理员存在
    auth_service.ensure_admin_user()
    user = auth_service.authenticate(username, password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    request.session["user_id"] = user.id
    permissions = auth_service.get_user_permissions(user.id)
    return LoginResponse(
        username=user.username,
        display_name=user.display_name,
        role_id=user.role_id,
        permissions=permissions,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # 确保默认角色和管理员存在
    AuthService(db).ensure_admin_user()
    return templates.TemplateResponse(request, "login.html")


@router.get("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=302, headers={"Location": "/login"})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    auth_service = AuthService(db)
    # 确保默认角色和管理员存在
    auth_service.ensure_admin_user()
    user = auth_service.authenticate(username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "partials/login_result.html", {"user": None}
        )
    request.session["user_id"] = user.id
    # 登录成功：让 HTMX 整页跳转到首页
    return Response(status_code=204, headers={"HX-Redirect": "/"})


# === 用户管理 ===
@router.get("/system/users", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    user_repo = UserRepository(db)
    role_repo = RoleRepository(db)
    users = user_repo.list_all_with_roles()
    roles = role_repo.list_all()
    return templates.TemplateResponse(
        request, "system/users.html", {"users": users, "roles": roles}
    )


@router.post("/system/users", response_class=HTMLResponse)
def create_user_page(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    role_id: int = Form(None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    auth_service = AuthService(db)
    try:
        user = auth_service.create_user(
            UserCreate(username=username, password=password, display_name=display_name, role_id=role_id)
        )
    except ValueError as e:
        return templates.TemplateResponse(
            request, "masterdata/partials/error_row.html", {"error": str(e), "colspan": 6}
        )
    return templates.TemplateResponse(
        request, "system/partials/user_row.html", {"user": user, "roles": RoleRepository(db).list_all()}
    )


# === 角色管理 ===
@router.get("/system/roles", response_class=HTMLResponse)
def roles_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if (r := _login_guard(request, db)): return r
    role_repo = RoleRepository(db)
    perm_repo = role_repo = RoleRepository(db)  # temp
    roles = role_repo.list_all()
    permissions = RoleRepository(db).list_all()  # temp fix
    from lightmes.modules.auth.repository import PermissionRepository
    permissions = PermissionRepository(db).list_all()
    return templates.TemplateResponse(
        request, "system/roles.html", {"roles": roles, "permissions": permissions}
    )
