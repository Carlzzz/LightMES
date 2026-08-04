import logging
from fastapi import FastAPI
from lightmes.shared.events import event_bus

logger = logging.getLogger("lightmes.trace")


def _on_station_passed(event) -> None:
    # MVP: 仅记录，证明事件总线连通；绑定在过站事务内同步完成，不靠本订阅。
    logger.debug("trace observed StationPassed: sn=%s", getattr(event, "sn", None))


def register(app: FastAPI) -> None:
    from lightmes.modules.trace.router import router
    from lightmes.modules.production.events import StationPassed

    app.include_router(router)
    event_bus.subscribe(StationPassed, _on_station_passed)
