from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from lightmes.modules.inventory.router import router

    app.include_router(router)
