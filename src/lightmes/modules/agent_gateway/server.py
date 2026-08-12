from fastmcp import FastMCP

mcp = FastMCP(
    name="LightMES",
    instructions=(
        "LightMES Manufacturing Execution System for notebook shell assembly. "
        "Use these tools to query production status, schedule work orders, "
        "and report defects. Most write operations require admin/supervisor role."
    ),
)


def mount_mcp(app) -> None:
    """Mount MCP server onto FastAPI app at /mcp.

    Implemented in Task 2 (requires auth dependency first).
    """
    pass
