from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.dependencies import require_api_key
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError

router = APIRouter(tags=["api-v1"])


@router.get("/api-keys", response_model=list[ApiKeyRead],
            tags=["API Keys"],
            dependencies=[Depends(require_api_key("read"))])
def list_api_keys(
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read")),
) -> list[ApiKeyRead]:
    """列出当前用户的 API Key（Bearer）或会话用户的 API Key。"""
    keys = ApiKeyService(db).list_for_user(user.id)
    return [ApiKeyRead.model_validate(k) for k in keys]


@router.post("/api-keys", response_model=ApiKeyCreatedResponse,
             tags=["API Keys"],
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key("read", "write"))])
def create_api_key(
    data: ApiKeyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read", "write")),
) -> ApiKeyCreatedResponse:
    """创建新的 API Key。full_key 仅此一次在响应中返回。"""
    full_key, record = ApiKeyService(db).create(
        name=data.name, user_id=user.id, scopes=data.scopes,
        expires_at=data.expires_at)
    db.commit()
    db.refresh(record)
    return ApiKeyCreatedResponse(
        id=record.id, name=record.name, key_prefix=record.key_prefix,
        scopes=record.scopes, full_key=full_key, created_at=record.created_at,
    )


@router.delete("/api-keys/{key_id}", tags=["API Keys"],
               status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_key("read", "write"))])
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read", "write")),
) -> None:
    """按 id 吊销 API Key。仅当前用户名下的 Key 才能被吊销。"""
    from lightmes.modules.auth.models import ApiKey
    target = db.get(ApiKey, key_id)
    if target is None:
        raise NotFoundError(f"API Key 不存在: {key_id}")
    if target.user_id != user.id:
        # IDOR 防护：不能吊销其他用户的 Key（与不存在同样响应，避免泄露）
        raise NotFoundError(f"API Key 不存在: {key_id}")
    ApiKeyService(db).revoke(key_id, revoked_by_user_id=user.id)
    db.commit()
    return None
