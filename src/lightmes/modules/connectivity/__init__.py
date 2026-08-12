from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Register connectivity admin routes."""
    from lightmes.modules.connectivity import models  # noqa: F401
    from lightmes.modules.connectivity.router import router
    app.include_router(router)
