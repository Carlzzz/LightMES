"""Re-export C-layer (api_v1) Read schemas for MCP tool input/output.

工具直接复用 api_v1 的 Read schemas，避免重复定义。Create / Patch 输入
直接用 tool 函数签名（FastMCP 把签名转为 JSON Schema），不引入额外模型。
"""
from pydantic import BaseModel, ConfigDict

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


class IssueActionReadV1(BaseModel):
    """CAPA / IssueAction 的轻量 Read（MCP 专用，不直接复用 api_v1）。"""
    id: int
    type: str
    title: str
    status: str
    assigned_to_id: int | None = None
    due_date: str | None = None


class IssueReadV1(BaseModel):
    """Issue Read（MCP 专用，含 issue_type_code + is_blocking 派生字段）。"""
    id: int
    issue_type_code: str
    title: str
    description: str | None
    status: str
    severity: str
    source: str
    serial_unit_id: int | None = None
    work_order_id: int | None = None
    work_station_id: int | None = None
    defect_id: int | None = None
    reported_at: str
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    closed_at: str | None = None
    is_blocking: bool = False

    model_config = ConfigDict(from_attributes=True)


class CreateIssueResult(BaseModel):
    """create_issue 返回结构。"""
    id: int
    status: str = "open"


class UpdateIssueStatusResult(BaseModel):
    """update_issue_status 返回结构。"""
    id: int
    status: str


__all__ = [
    "ApiKeyCreate",
    "ApiKeyCreatedResponse",
    "ApiKeyRead",
    "BacklogItem",
    "BacklogResult",
    "CreateAndScheduleResult",
    "CreateIssueResult",
    "DefectReadV1",
    "DefectTypeReadV1",
    "IssueActionReadV1",
    "IssueReadV1",
    "ProductionStatusResult",
    "ReportDefectResult",
    "SerialUnitReadV1",
    "UpdateIssueStatusResult",
    "WorkOrderCreateV1",
    "WorkOrderPriorityPatch",
    "WorkOrderReadV1",
]
