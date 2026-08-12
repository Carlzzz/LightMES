"""Serial Unit MCP tools (3 read wrappers)."""
from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import SerialUnitReadV1
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def list_serial_units(
    work_order_id: int | None = None,
    status: list[str] | None = None,
    sn: str | None = None,
    page: int = 1,
    size: int = 20,
) -> list[SerialUnitReadV1]:
    """列出 SN 单元，可按 work_order_id/status/sn 模糊过滤。

    Args:
        work_order_id: 可选，按工单过滤。
        status: 可选，按状态过滤（如 ["in_process", "passed"]）。
        sn: 可选，SN 子串模糊匹配（ILIKE）。
        page: 页码，从 1 开始。
        size: 每页数量。

    Returns:
        SerialUnit 列表，按 id desc 排序。
    """
    from fastmcp.server.dependencies import get_http_request
    from sqlalchemy import select

    from lightmes.modules.production.models import SerialUnit

    db = get_http_request().state.db_session
    q = select(SerialUnit).order_by(SerialUnit.id.desc())
    if work_order_id is not None:
        q = q.where(SerialUnit.work_order_id == work_order_id)
    if status:
        q = q.where(SerialUnit.status.in_(status))
    if sn:
        q = q.where(SerialUnit.sn.ilike(f"%{sn}%"))
    rows = list(
        db.execute(q.offset((page - 1) * size).limit(size)).scalars().all()
    )
    return [SerialUnitReadV1.model_validate(r) for r in rows]


@mcp.tool()
@require_scope("read")
def get_serial_unit(serial_unit_id: int) -> SerialUnitReadV1:
    """按 id 查询 SN 单元。

    Args:
        serial_unit_id: SN 单元 id。

    Raises:
        NotFoundError: 不存在。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.models import SerialUnit
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    su = db.get(SerialUnit, serial_unit_id)
    if su is None:
        raise NotFoundError(f"Serial unit 不存在: {serial_unit_id}")
    return SerialUnitReadV1.model_validate(su)


@mcp.tool()
@require_scope("read")
def get_serial_unit_by_sn(sn: str) -> SerialUnitReadV1:
    """按 SN 业务键查询。

    Args:
        sn: 序列号字符串。

    Raises:
        NotFoundError: 不存在。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    return SerialUnitReadV1.model_validate(su)
