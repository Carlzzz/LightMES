"""Work Order MCP tools (thin wrappers over production service / models).

4 tools:
- list_work_orders (read)
- get_work_order (read)
- create_work_order (write — admin/supervisor)
- patch_work_order_priority (write — admin/supervisor)

工具实现原则：薄包装。直接通过 `get_http_request().state.db_session` 拿到
DB session（由 `server.py` 的 Bearer 中间件注入），不重新发明 service 层。
"""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import WorkOrderReadV1
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_work_orders(
    page: int = 1,
    size: int = 20,
    status: list[str] | None = None,
    line_id: int | None = None,
) -> list[WorkOrderReadV1]:
    """列出工单，分页 + 过滤（status/line_id）。

    Args:
        page: 页码，从 1 开始。
        size: 每页数量。
        status: 可选，按状态过滤（如 ["created", "in_progress"]）。
        line_id: 可选，按产线过滤。

    Returns:
        WorkOrder 列表，按 id desc 排序。
    """
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select

    from lightmes.modules.production.models import WorkOrder

    db = get_http_request().state.db_session
    q = select(WorkOrder).order_by(WorkOrder.id.desc())
    if status:
        q = q.where(WorkOrder.status.in_(status))
    if line_id is not None:
        q = q.where(WorkOrder.line_id == line_id)
    rows = list(
        db.execute(q.offset((page - 1) * size).limit(size)).scalars().all()
    )
    return [WorkOrderReadV1.model_validate(r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_work_order(work_order_id: int) -> WorkOrderReadV1:
    """按 id 查询工单。

    Args:
        work_order_id: 工单 id。

    Raises:
        NotFoundError: 工单不存在。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.models import WorkOrder
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {work_order_id}")
    return WorkOrderReadV1.model_validate(wo)


@mcp.tool()
@require_scope("write")
def create_work_order(
    code: str,
    product_id: int,
    routing_id: int,
    line_id: int,
    qty: int,
    sn_rule_id: int | None = None,
    priority: int = 5,
) -> WorkOrderReadV1:
    """创建工单（write scope，需 admin/supervisor 角色）。

    Args:
        code: 工单编码，唯一。
        product_id: 产品 id。
        routing_id: 工艺路线 id。
        line_id: 产线 id。
        qty: 计划数量（>0）。
        sn_rule_id: 可选，SN 生成规则 id。
        priority: 优先级 1-9，默认 5。

    Returns:
        创建后的工单。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.schemas import WorkOrderCreate
    from lightmes.modules.production.service import ProductionService

    db = get_http_request().state.db_session
    wo = ProductionService(db).create_work_order(WorkOrderCreate(
        code=code, product_id=product_id, routing_id=routing_id,
        line_id=line_id, qty=qty, sn_rule_id=sn_rule_id))
    # priority 默认 5 由 model 决定；如调用方指定非默认值则覆盖
    if priority != 5:
        wo.priority = priority
        db.flush()
    return WorkOrderReadV1.model_validate(wo)


@mcp.tool()
@require_scope("write")
def patch_work_order_priority(
    work_order_id: int, priority: int
) -> WorkOrderReadV1:
    """调整工单优先级（1-9）。

    Args:
        work_order_id: 工单 id。
        priority: 新优先级，必须 1-9。

    Raises:
        ValidationError: priority 越界。
        NotFoundError: 工单不存在。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.models import WorkOrder
    from lightmes.shared.errors import NotFoundError, ValidationError

    if not (1 <= priority <= 9):
        raise ValidationError("priority 必须在 1-9 之间")
    db = get_http_request().state.db_session
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise NotFoundError(f"工单不存在: {work_order_id}")
    wo.priority = priority
    db.flush()
    return WorkOrderReadV1.model_validate(wo)
