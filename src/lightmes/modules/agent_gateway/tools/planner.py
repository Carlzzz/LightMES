"""Compose tools: list_backlog, create_and_schedule_work_order.

- list_backlog: read scope, 列出未排程工单（planned_start IS NULL 或 line_id IS NULL），
  并附 product code/name 以便 Agent 直接读懂。
- create_and_schedule_work_order: write scope, 自动解析 product/line/routing/sn_rule
  并创建 + 排程。冲突时不 re-raise，返回 conflict dict 让 Agent 决策。

实现说明：
- SnRule 解析优先取 product 专属规则，回退到全局规则（product_id IS NULL）。
  测试场景中 `_env` fixture 创建 SnRule 时不带 product_id；生产中可能配置专属规则。
- 冲突捕获指定 ConflictError 而非泛 Exception，避免吞掉其它业务异常。
"""
from datetime import datetime

from sqlalchemy import select

from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    BacklogItem, BacklogResult, CreateAndScheduleResult, WorkOrderReadV1,
)
from lightmes.modules.agent_gateway.server import mcp
from lightmes.shared.errors import ConflictError


@mcp.tool()
@require_scope("read")
def list_backlog(line_id: int | None = None) -> BacklogResult:
    """列出未排程工单（planned_start IS NULL 或 line_id IS NULL）。

    按 priority desc 排序。每条附带 product code/name，避免 Agent 二次查询。

    Args:
        line_id: 可选，按产线过滤。

    Returns:
        BacklogResult：backlog 列表 + total。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.masterdata.query_service import MasterDataQueryService
    from lightmes.modules.production.planner_service import PlannerService

    db = get_http_request().state.db_session
    wos = PlannerService(db).list_backlog(line_id=line_id)
    query = MasterDataQueryService(db)
    items = []
    for wo in wos:
        p = query.get_product(wo.product_id)
        items.append(BacklogItem(
            id=wo.id, code=wo.code, priority=wo.priority, qty=wo.qty,
            product_code=p.code if p else None,
            product_name=p.name if p else None,
        ))
    return BacklogResult(backlog=items, total=len(items))


@mcp.tool()
@require_scope("write")
def create_and_schedule_work_order(
    product_code: str,
    qty: int,
    line_code: str,
    planned_start: str,
    planned_end: str,
    priority: int = 5,
    force_conflict: bool = False,
) -> CreateAndScheduleResult:
    """创建工单 + 排到产线时段（write scope，需 admin/supervisor）。

    自动解析 product_code / line_code，自动选 active routing + sn_rule。
    若时段冲突，返回 conflict 信息（除非 force_conflict=True）；不 re-raise，
    让 Agent 拿到结构化 conflict dict 自行决策（改时段 / 改产线 / 强制覆盖）。

    Args:
        product_code: 产品编码。
        qty: 工单数量（>0）。
        line_code: 产线编码。
        planned_start: ISO 8601 起始时间。
        planned_end: ISO 8601 结束时间。
        priority: 1-9，默认 5（数字越大越优先）。
        force_conflict: 是否强制覆盖冲突（需 supervisor 角色，本工具暂不单独鉴权）。

    Returns:
        CreateAndScheduleResult：work_order + scheduled + conflict。

    Raises:
        NotFoundError: 产品 / 产线 / active routing / sn_rule 不存在。
        ValidationError: 时间格式错误或产品未配置 SN 规则。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.masterdata.repository import (
        LineRepository, ProductRepository, RoutingRepository,
    )
    from lightmes.modules.production.models import SnRule
    from lightmes.modules.production.planner_service import PlannerService
    from lightmes.modules.production.schemas import WorkOrderCreate
    from lightmes.modules.production.service import ProductionService
    from lightmes.shared.errors import NotFoundError, ValidationError

    db = get_http_request().state.db_session
    user = get_http_request().state.user

    # 1. 解析 product（按 code）
    product = ProductRepository(db).get_by_code(product_code)
    if product is None:
        raise NotFoundError(f"产品不存在: {product_code}")

    # 2. 解析 line（按 code）
    line = LineRepository(db).get_by_code(line_code)
    if line is None:
        raise NotFoundError(f"产线不存在: {line_code}")

    # 3. 选 active routing
    routing = RoutingRepository(db).get_active_by_product(product.id)
    if routing is None:
        raise NotFoundError(f"产品无 active routing: {product_code}")

    # 4. 选 sn_rule（先 product 专属，回退全局 product_id IS NULL）
    sn_rules = list(db.execute(
        select(SnRule).where(SnRule.product_id == product.id)
    ).scalars().all())
    if not sn_rules:
        sn_rules = list(db.execute(
            select(SnRule).where(SnRule.product_id.is_(None))
        ).scalars().all())
    if not sn_rules:
        raise ValidationError(f"产品未配置 SN 规则: {product_code}")
    sn_rule_id = sn_rules[0].id

    # 5. 解析时间
    try:
        start_dt = datetime.fromisoformat(planned_start)
        end_dt = datetime.fromisoformat(planned_end)
    except ValueError as e:
        raise ValidationError(f"时间格式错误（需 ISO 8601）: {e}")

    # 6. 创建 WO（自动生成唯一 code，避免与既有冲突）
    wo = ProductionService(db).create_work_order(WorkOrderCreate(
        code=f"{product_code}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        product_id=product.id, routing_id=routing.id, line_id=line.id,
        qty=qty, sn_rule_id=sn_rule_id))
    wo.priority = priority
    db.flush()

    # 7. 排程（冲突时不抛出，捕获并返回 conflict dict；其它异常回滚整个事务，
    #    避免 flush 过的 WO 在 session 关闭时被半提交，留下无 schedule 的孤儿 WO）
    conflict: dict | None = None
    try:
        PlannerService(db).schedule(
            wo.id, line.id, start_dt, end_dt,
            user_id=user.id, force=force_conflict)
    except ConflictError as e:
        conflict = {"error": e.detail}
    except Exception:
        db.rollback()
        raise
    db.commit()
    db.refresh(wo)
    return CreateAndScheduleResult(
        work_order=WorkOrderReadV1.model_validate(wo),
        scheduled=conflict is None,
        conflict=conflict,
    )
