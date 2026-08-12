"""Re-export C-layer (api_v1) Read schemas for MCP tool input/output.

工具直接复用 api_v1 的 Read schemas，避免重复定义。Create / Patch 输入
直接用 tool 函数签名（FastMCP 把签名转为 JSON Schema），不引入额外模型。
"""
from lightmes.modules.api_v1.schemas import (
    ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead,
    DefectReadV1,
    SerialUnitReadV1,
    WorkOrderCreateV1, WorkOrderPriorityPatch, WorkOrderReadV1,
)

__all__ = [
    "ApiKeyCreate",
    "ApiKeyCreatedResponse",
    "ApiKeyRead",
    "DefectReadV1",
    "SerialUnitReadV1",
    "WorkOrderCreateV1",
    "WorkOrderPriorityPatch",
    "WorkOrderReadV1",
]
