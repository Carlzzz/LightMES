"""All MCP tools. Import triggers registration on `mcp` instance.

每个 tool 模块在 import 时执行 `@mcp.tool()` 装饰器从而完成注册。
`server.mount_mcp()` 在调用 `mcp.http_app()` 前会 import 本模块。
"""
from lightmes.modules.agent_gateway.tools import (  # noqa: F401
    api_keys,
    defect_types,
    defects,
    query,
    serial_units,
    work_orders,
)
