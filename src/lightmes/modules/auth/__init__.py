from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Wire this module into the app: routers now, event-bus subscribers as they are added."""
    from lightmes.modules.auth.router import router

    app.include_router(router)
    # Future: event_bus.subscribe(SomeEvent, handler) goes here.
