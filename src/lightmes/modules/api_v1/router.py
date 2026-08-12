from datetime import datetime
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.dependencies import require_api_key
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError

router = APIRouter()


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


# ---- Work Orders ----

_WO_TAG = "Work Orders"
_LIST_MAX_SIZE = 100


@router.get("/work-orders", response_model=list[WorkOrderReadV1], tags=[_WO_TAG])
def list_work_orders(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_LIST_MAX_SIZE),
    status: list[str] = Query(default=[]),
    line_id: int | None = Query(default=None),
    created_since: datetime | None = Query(default=None),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[WorkOrderReadV1]:
    """列出工单（分页 + 过滤）。

    通过 `X-Total-Count` / `X-Page` / `X-Size` 响应头返回分页信息。
    """
    from sqlalchemy import select, func
    from lightmes.modules.production.models import WorkOrder
    q = select(WorkOrder).order_by(WorkOrder.id.desc())
    if status:
        q = q.where(WorkOrder.status.in_(status))
    if line_id is not None:
        q = q.where(WorkOrder.line_id == line_id)
    if created_since is not None:
        q = q.where(WorkOrder.created_at >= created_since)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Size"] = str(size)
    return [WorkOrderReadV1.model_validate(r) for r in rows]


@router.get("/work-orders/{wo_id}", response_model=WorkOrderReadV1, tags=[_WO_TAG])
def get_work_order(
    wo_id: int,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> WorkOrderReadV1:
    from lightmes.modules.production.models import WorkOrder
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {wo_id}")
    return WorkOrderReadV1.model_validate(wo)


@router.post("/work-orders", response_model=WorkOrderReadV1,
             status_code=status.HTTP_201_CREATED, tags=[_WO_TAG])
def create_work_order(
    data: WorkOrderCreateV1,
    user: User = Depends(require_api_key("read", "write")),
    db: Session = Depends(get_db),
) -> WorkOrderReadV1:
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate
    wo = ProductionService(db).create_work_order(WorkOrderCreate(
        code=data.code, product_id=data.product_id, routing_id=data.routing_id,
        line_id=data.line_id, qty=data.qty, sn_rule_id=data.sn_rule_id))
    wo.priority = data.priority
    db.commit()
    db.refresh(wo)
    return WorkOrderReadV1.model_validate(wo)


@router.patch("/work-orders/{wo_id}/priority",
              response_model=WorkOrderReadV1, tags=[_WO_TAG])
def patch_work_order_priority(
    wo_id: int,
    data: WorkOrderPriorityPatch,
    user: User = Depends(require_api_key("read", "write")),
    db: Session = Depends(get_db),
) -> WorkOrderReadV1:
    from lightmes.modules.production.models import WorkOrder
    wo = db.get(WorkOrder, wo_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {wo_id}")
    wo.priority = data.priority
    db.commit()
    db.refresh(wo)
    return WorkOrderReadV1.model_validate(wo)
