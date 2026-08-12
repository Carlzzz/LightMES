from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Mount MCP server at /mcp. Filled in by later tasks."""
    from lightmes.modules.agent_gateway.server import mount_mcp

    mount_mcp(app)
