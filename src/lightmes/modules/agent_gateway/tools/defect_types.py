"""Defect type MCP tool (1 read wrapper).

list_defect_types —— 让 Agent 在调用 report_defect_for_sn 前发现可用的
defect_type_code 字典值。默认仅返回 active 类型。
"""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import DefectTypeReadV1
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_defect_types(
    is_active: bool | None = True,
) -> list[DefectTypeReadV1]:
    """列出缺陷类型（默认仅 active）。用于让 Agent 知道可用的 defect_type_code。

    Args:
        is_active: 默认 True 仅返回 active；传 None 返回全部；传 False 仅返回 inactive。

    Returns:
        DefectType 列表，按 id asc 排序。
    """
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select

    from lightmes.modules.production.models import DefectType

    db = get_http_request().state.db_session
    q = select(DefectType).order_by(DefectType.id)
    if is_active is not None:
        q = q.where(DefectType.is_active == is_active)
    rows = list(db.execute(q).scalars().all())
    return [DefectTypeReadV1.model_validate(r) for r in rows]
