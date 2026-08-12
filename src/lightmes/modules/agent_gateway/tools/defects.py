"""Defect MCP tools (2 read wrappers + 1 compose write)."""
from sqlalchemy import select

from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    DefectReadV1, ReportDefectResult,
)
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


@mcp.tool()
@require_scope("write")
def report_defect_for_sn(
    sn: str,
    defect_type_code: str,
    remark: str | None = None,
    position: str | None = None,
) -> ReportDefectResult:
    """按 SN 登记缺陷（write scope）。登记后 SN 自动隔离（status=quarantined）。

    defect_type_code 必须先用 list_defect_types 工具查询可用的 code。

    Args:
        sn: 序列号。
        defect_type_code: 缺陷类型编码（须 active）。
        remark: 可选备注。
        position: 可选缺陷位置描述。

    Returns:
        ReportDefectResult：defect_record + serial_unit_status（通常为 "quarantined"）。

    Raises:
        NotFoundError: SN 或缺陷类型不存在 / 缺陷类型已停用。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.production.defect_service import DefectService
    from lightmes.modules.production.models import DefectType
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.shared.errors import NotFoundError

    db = get_http_request().state.db_session
    user = get_http_request().state.user

    su = SerialUnitRepository(db).get_by_sn(sn)
    if su is None:
        raise NotFoundError(f"SN 不存在: {sn}")
    dt = db.execute(
        select(DefectType).where(DefectType.code == defect_type_code)
    ).scalar_one_or_none()
    if dt is None or not dt.is_active:
        raise NotFoundError(f"缺陷类型不存在或已停用: {defect_type_code}")

    # operation_id 必须是 operations.id 的有效 FK；SN 的 current_operation_seq
    # 是序号不是 id，故这里传 None（缺陷登记不需要严格关联工序行）。
    record = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=sn, discovered_by=user.id,
        operation_id=None, work_station_id=None,
        position=position, remark=remark)
    db.commit()
    db.refresh(record)
    db.refresh(su)
    return ReportDefectResult(
        defect_record=DefectReadV1.model_validate(record),
        serial_unit_status=su.status,
    )
