from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from lightmes.modules.masterdata.api_router import router as api_router
    from lightmes.modules.masterdata.page_router import router as page_router

    app.include_router(api_router)
    app.include_router(page_router)
