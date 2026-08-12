"""Defect MCP tools (2 read wrappers)."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import DefectReadV1
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_defects(
    handling_status: list[str] | None = None,
    severity: list[str] | None = None,
    work_order_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> list[DefectReadV1]:
    """列出缺陷记录，可按 handling_status/severity/work_order_id 过滤。

    Args:
        handling_status: 可选，如 ["pending", "rework", "scrap", "concession"]。
        severity: 可选，如 ["critical", "major", "minor"]。
        work_order_id: 可选，按工单过滤。
        page: 页码，从 1 开始。
        size: 每页数量。

    Returns:
        Defect 列表，按 id desc 排序。
    """
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select

    from lightmes.modules.production.models import DefectRecord

    db = get_http_request().state.db_session
    q = select(DefectRecord).order_by(DefectRecord.id.desc())
    if handling_status:
        q = q.where(DefectRecord.handling_status.in_(handling_status))
    if severity:
        q = q.where(DefectRecord.severity.in_(severity))
    if work_order_id is not None:
        q = q.where(DefectRecord.work_order_id == work_order_id)
    rows = list(
        db.execute(q.offset((page - 1) * size).limit(size)).scalars().all()
    )
    return [DefectReadV1.model_validate(r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_defect(defect_id: int) -> DefectReadV1:
    """按 id 查询缺陷记录。

    Args:
        defect_id: 缺陷记录 id。

    Raises:
        NotFoundError: 不存在。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.models import DefectRecord
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    d = db.get(DefectRecord, defect_id)
    if d is None:
        raise NotFoundError(f"缺陷不存在: {defect_id}")
    return DefectReadV1.model_validate(d)
