from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """注册 /api/v1/* 路由。后续 task 填充具体路由。"""
    from lightmes.modules.api_v1.router import router

    app.include_router(router, prefix="/api/v1")
