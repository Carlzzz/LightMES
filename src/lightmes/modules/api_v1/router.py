from datetime import datetime
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from lightmes.database import get_db
from lightmes.modules.api_v1.api_key_service import ApiKeyService
from lightmes.modules.api_v1.dependencies import require_api_key
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1, DefectTypeReadV1, SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)
from lightmes.modules.auth.models import User
from lightmes.shared.errors import NotFoundError

router = APIRouter()


@router.get("/api-keys", response_model=list[ApiKeyRead],
            tags=["API Keys"])
def list_api_keys(
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key("read")),
) -> list[ApiKeyRead]:
    """列出当前用户的 API Key（Bearer）或会话用户的 API Key。"""
    keys = ApiKeyService(db).list_for_user(user.id)
    return [ApiKeyRead.model_validate(k) for k in keys]


@router.post("/api-keys", response_model=ApiKeyCreatedResponse,
             tags=["API Keys"],
             status_code=status.HTTP_201_CREATED)
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
               status_code=status.HTTP_204_NO_CONTENT)
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
    status: list[str] = Query(default=[], max_length=20),
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


# ---- Serial Units ----

_SU_TAG = "Serial Units"


@router.get("/serial-units", response_model=list[SerialUnitReadV1], tags=[_SU_TAG])
def list_serial_units(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_LIST_MAX_SIZE),
    work_order_id: int | None = Query(default=None),
    status: list[str] = Query(default=[], max_length=20),
    sn: str | None = Query(default=None),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[SerialUnitReadV1]:
    """列出 serial units（分页 + 过滤）。

    通过 `X-Total-Count` / `X-Page` / `X-Size` 响应头返回分页信息。
    """
    from sqlalchemy import select, func
    from lightmes.modules.production.models import SerialUnit
    q = select(SerialUnit).order_by(SerialUnit.id.desc())
    if work_order_id is not None:
        q = q.where(SerialUnit.work_order_id == work_order_id)
    if status:
        q = q.where(SerialUnit.status.in_(status))
    if sn:
        q = q.where(SerialUnit.sn.ilike(f"%{sn}%"))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Size"] = str(size)
    return [SerialUnitReadV1.model_validate(r) for r in rows]


@router.get("/serial-units/by-sn/{sn}", response_model=SerialUnitReadV1, tags=[_SU_TAG])
def get_serial_unit_by_sn(
    sn: str,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> SerialUnitReadV1:
    """按 SN（业务键）查询 serial unit。"""
    from lightmes.modules.production.repository import SerialUnitRepository
    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    return SerialUnitReadV1.model_validate(su)


@router.get("/serial-units/{su_id}", response_model=SerialUnitReadV1, tags=[_SU_TAG])
def get_serial_unit(
    su_id: int,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> SerialUnitReadV1:
    """按 id 获取 serial unit。"""
    from lightmes.modules.production.models import SerialUnit
    su = db.get(SerialUnit, su_id)
    if su is None:
        raise NotFoundError(f"Serial unit 不存在: {su_id}")
    return SerialUnitReadV1.model_validate(su)


# ---- Defects ----

_DEF_TAG = "Defects"


@router.get("/defects", response_model=list[DefectReadV1], tags=[_DEF_TAG])
def list_defects(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=_LIST_MAX_SIZE),
    handling_status: list[str] = Query(default=[], max_length=20),
    severity: list[str] = Query(default=[], max_length=20),
    work_order_id: int | None = Query(default=None),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[DefectReadV1]:
    """列出缺陷记录（分页 + 过滤）。

    通过 `X-Total-Count` / `X-Page` / `X-Size` 响应头返回分页信息。
    """
    from sqlalchemy import select, func
    from lightmes.modules.production.models import DefectRecord
    q = select(DefectRecord).order_by(DefectRecord.id.desc())
    if handling_status:
        q = q.where(DefectRecord.handling_status.in_(handling_status))
    if severity:
        q = q.where(DefectRecord.severity.in_(severity))
    if work_order_id is not None:
        q = q.where(DefectRecord.work_order_id == work_order_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q.offset((page - 1) * size).limit(size)).scalars().all())
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Size"] = str(size)
    return [DefectReadV1.model_validate(r) for r in rows]


@router.get("/defects/{defect_id}", response_model=DefectReadV1, tags=[_DEF_TAG])
def get_defect(
    defect_id: int,
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> DefectReadV1:
    """按 id 获取缺陷记录。"""
    from lightmes.modules.production.models import DefectRecord
    d = db.get(DefectRecord, defect_id)
    if d is None:
        raise NotFoundError(f"缺陷不存在: {defect_id}")
    return DefectReadV1.model_validate(d)


# ---- Defect Types ----

_DT_TAG = "Defect Types"


@router.get("/defect-types", response_model=list[DefectTypeReadV1], tags=[_DT_TAG])
def list_defect_types(
    response: Response,
    is_active: bool | None = Query(default=None),
    category: list[str] = Query(default=[], max_length=20),
    user: User = Depends(require_api_key("read")),
    db: Session = Depends(get_db),
) -> list[DefectTypeReadV1]:
    """列出缺陷类型。可按 is_active / category 过滤。

    通过 `X-Total-Count` 响应头返回总数。字典表数据量小，不分页。
    """
    from sqlalchemy import select, func
    from lightmes.modules.production.models import DefectType
    q = select(DefectType).order_by(DefectType.id.desc())
    if is_active is not None:
        q = q.where(DefectType.is_active == is_active)
    if category:
        q = q.where(DefectType.category.in_(category))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = list(db.execute(q).scalars().all())  # 无分页（数量小）
    response.headers["X-Total-Count"] = str(total)
    return [DefectTypeReadV1.model_validate(r) for r in rows]
