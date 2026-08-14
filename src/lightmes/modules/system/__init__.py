from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Register system maintenance and backup routes."""
    from lightmes.modules.system.backup import router

    app.include_router(router)
