from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
    current_user_or_none, html_role_guard,
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


def _is_admin(user) -> bool:
    """检查用户是否为管理员（兼容新 role_obj 与 legacy role 字段）。"""
    if user is None:
        return False
    if user.role_obj is not None:
        return user.role_obj.name == "admin"
    return getattr(user, "role", None) == "admin"


@router.post("/api/auth/login", response_model=LoginResponse)
def api_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> LoginResponse:
    auth_service = AuthService(db)
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
    _, r = html_role_guard(request, db, "admin")
    if r is not None:
        return r
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
    _, r = html_role_guard(request, db, "admin")
    if r is not None:
        return r
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
    _, r = html_role_guard(request, db, "admin")
    if r is not None:
        return r
    role_repo = RoleRepository(db)
    perm_repo = role_repo = RoleRepository(db)  # temp
    roles = role_repo.list_all()
    permissions = RoleRepository(db).list_all()  # temp fix
    from lightmes.modules.auth.repository import PermissionRepository
    permissions = PermissionRepository(db).list_all()
    return templates.TemplateResponse(
        request, "system/roles.html", {"roles": roles, "permissions": permissions}
    )


# ---- API Key 管理（admin only）----

@router.get("/system/api-keys", response_class=HTMLResponse)
def api_keys_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    if not _is_admin(user):
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    keys = ApiKeyService(db).list_for_user(user.id)
    return templates.TemplateResponse(
        request, "system/api_keys.html", {"keys": keys, "user": user},
    )


@router.post("/system/api-keys", response_class=HTMLResponse)
def api_key_create(
    request: Request,
    name: str = Form(...),
    scopes: list[str] = Form([]),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    if not _is_admin(user):
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    scopes_resolved = scopes if scopes else ["read"]
    full_key, record = ApiKeyService(db).create(
        name=name, user_id=user.id, scopes=scopes_resolved,
    )
    db.commit()
    return templates.TemplateResponse(
        request, "system/partials/api_key_created.html",
        {"full_key": full_key, "record": record},
    )


@router.post("/system/api-keys/{key_id}/revoke", response_class=HTMLResponse)
def api_key_revoke(
    request: Request,
    key_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = current_user_or_none(request, db)
    if user is None:
        return HTMLResponse("请先登录", status_code=401)
    if not _is_admin(user):
        return HTMLResponse("权限不足", status_code=403)
    from lightmes.modules.api_v1.api_key_service import ApiKeyService
    from lightmes.modules.auth.models import ApiKey
    target = db.get(ApiKey, key_id)
    if target is None or target.user_id != user.id:
        # IDOR 防护：与 JSON 路由保持一致，不存在与其他用户的不做区分
        return HTMLResponse("API Key 不存在", status_code=404)
    ApiKeyService(db).revoke(key_id, revoked_by_user_id=user.id)
    db.commit()
    return RedirectResponse(url="/system/api-keys", status_code=303)
