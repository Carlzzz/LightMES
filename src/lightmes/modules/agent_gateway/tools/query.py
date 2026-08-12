"""Compose tool: query_production_status.

第一个 compose 工具 —— 把多个 C-layer 查询聚合到一次返回，避免 Agent 手工
compose 多个 API 调用。读 scope only；无写副作用，故不需要 `_has_write_role`。
"""
from datetime import datetime

from sqlalchemy import select

from lightmes.modules.agent_gateway.auth import require_scope
from lightmes.modules.agent_gateway.schemas import (
    DefectReadV1, ProductionStatusResult, SerialUnitReadV1, WorkOrderReadV1,
)
from lightmes.modules.agent_gateway.server import mcp


@mcp.tool()
@require_scope("read")
def query_production_status(
    work_order_code: str | None = None,
    sn: str | None = None,
    work_order_id: int | None = None,
) -> ProductionStatusResult:
    """查询生产状态：工单进度、最近缺陷、产线、超期状态。

    三种识别方式任选一：``work_order_code`` / ``sn`` / ``work_order_id``。
    返回综合视图，无需 Agent 手工 compose 多个 API 调用。

    Args:
        work_order_code: 工单编码（与 sn/work_order_id 三选一）。
        sn: 序列号；若提供则附带 serial_unit 字段。
        work_order_id: 工单 id。

    Returns:
        ProductionStatusResult：WO + produced/planned/progress + 最近 5 条
        缺陷 + is_overdue + line（dict {id,code,name}）+ 可选 serial_unit。

    Raises:
        ValidationError: 未提供任何识别符。
        NotFoundError: 工单不存在。
    """
    from fastmcp.server.dependencies import get_http_request

    from lightmes.modules.masterdata.models import Line
    from lightmes.modules.production.models import DefectRecord, WorkOrder
    from lightmes.modules.production.repository import SerialUnitRepository
    from lightmes.shared.errors import NotFoundError, ValidationError

    if not any([work_order_code, sn, work_order_id]):
        raise ValidationError(
            "必须提供 work_order_code / sn / work_order_id 之一"
        )

    db = get_http_request().state.db_session

    # 1. 解析 WO（按 id > code > sn 优先级）
    wo: WorkOrder | None = None
    su = None
    if work_order_id is not None:
        wo = db.get(WorkOrder, work_order_id)
    elif work_order_code is not None:
        wo = db.execute(
            select(WorkOrder).where(WorkOrder.code == work_order_code)
        ).scalar_one_or_none()
    elif sn is not None:
        su = SerialUnitRepository(db).get_by_sn(sn)
        if su is not None:
            wo = db.get(WorkOrder, su.work_order_id)
    if wo is None:
        raise NotFoundError(
            f"工单不存在: code={work_order_code} sn={sn} id={work_order_id}"
        )

    # 2. 聚合进度
    produced = wo.produced_qty or 0
    planned = wo.qty or 0
    progress = int(produced * 100 / planned) if planned > 0 else 0

    # 3. 最近 5 条缺陷（按 id desc）
    recent_defects = list(
        db.execute(
            select(DefectRecord)
            .where(DefectRecord.work_order_id == wo.id)
            .order_by(DefectRecord.id.desc())
            .limit(5)
        ).scalars().all()
    )

    # 4. 产线（dict 视图，仅暴露 id/code/name）
    line: dict | None = None
    if wo.line_id is not None:
        line_obj = db.get(Line, wo.line_id)
        if line_obj is not None:
            line = {
                "id": line_obj.id,
                "code": line_obj.code,
                "name": line_obj.name,
            }

    # 5. 超期判定：planned_end 已过 + 未完工
    is_overdue = bool(
        wo.planned_end is not None
        and wo.planned_end < datetime.now()
        and produced < planned
    )

    return ProductionStatusResult(
        work_order=WorkOrderReadV1.model_validate(wo),
        produced_qty=produced,
        planned_qty=planned,
        progress_percent=progress,
        recent_defects=[DefectReadV1.model_validate(d) for d in recent_defects],
        is_overdue=is_overdue,
        line=line,
        serial_unit=SerialUnitReadV1.model_validate(su) if su else None,
    )
