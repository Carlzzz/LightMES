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


class BacklogItem(BaseModel):
    """list_backlog 返回的单条 item。"""
    id: int
    code: str
    priority: int
    qty: int
    product_code: str | None
    product_name: str | None


class BacklogResult(BaseModel):
    """list_backlog 返回结构。"""
    backlog: list[BacklogItem]
    total: int


class CreateAndScheduleResult(BaseModel):
    """create_and_schedule_work_order 返回结构。

    scheduled=False + conflict != None 表示时段冲突，工单已创建但未排程。
    """
    work_order: WorkOrderReadV1
    scheduled: bool
    conflict: dict | None = None  # {"error": str} 形如 "产线 X 时段 ... 被工单 Y 占用"


class ReportDefectResult(BaseModel):
    """report_defect_for_sn 返回结构。"""
    defect_record: DefectReadV1
    serial_unit_status: str


__all__ = [
    "ApiKeyCreate",
    "ApiKeyCreatedResponse",
    "ApiKeyRead",
    "BacklogItem",
    "BacklogResult",
    "CreateAndScheduleResult",
    "DefectReadV1",
    "DefectTypeReadV1",
    "ProductionStatusResult",
    "ReportDefectResult",
    "SerialUnitReadV1",
    "WorkOrderCreateV1",
    "WorkOrderPriorityPatch",
    "WorkOrderReadV1",
]
