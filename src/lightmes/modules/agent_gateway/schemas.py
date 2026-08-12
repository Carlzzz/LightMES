"""Re-export C-layer (api_v1) Read schemas for MCP tool input/output.

工具直接复用 api_v1 的 Read schemas，避免重复定义。Create / Patch 输入
直接用 tool 函数签名（FastMCP 把签名转为 JSON Schema），不引入额外模型。
"""
from pydantic import BaseModel

from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1, DefectTypeReadV1, SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)


class ProductionStatusResult(BaseModel):
    """query_production_status 工具的返回结构。

    compose 视图：把 WO + 进度 + 最近缺陷 + 产线 + 超期状态聚合到一次返回，
    避免 Agent 调多个 API 自行 compose。serial_unit 仅在按 sn 查询时附带。
    """
    work_order: WorkOrderReadV1
    produced_qty: int
    planned_qty: int
    progress_percent: int
    recent_defects: list[DefectReadV1]
    is_overdue: bool
    line: dict | None  # {"id", "code", "name"}
    serial_unit: SerialUnitReadV1 | None = None


__all__ = [
    "ApiKeyCreate",
    "ApiKeyCreatedResponse",
    "ApiKeyRead",
    "DefectReadV1",
    "DefectTypeReadV1",
    "ProductionStatusResult",
    "SerialUnitReadV1",
    "WorkOrderCreateV1",
    "WorkOrderPriorityPatch",
    "WorkOrderReadV1",
]
