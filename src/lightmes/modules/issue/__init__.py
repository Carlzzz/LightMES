"""Issue / Andon 异常管理模块."""

from lightmes.modules.issue import router


def register(app):
    app.include_router(router.router)
