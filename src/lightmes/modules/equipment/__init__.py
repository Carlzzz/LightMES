from fastapi import FastAPI

from lightmes.modules.equipment.models import DowntimeReason

SYSTEM_DOWNTIME_REASONS = [
    {"code": "AUTO-FAULT", "name": "设备故障(自动)", "kind": "unplanned"},
    {"code": "AUTO-STOP", "name": "设备停机(自动)", "kind": "unplanned"},
    {"code": "AUTO-WAIT", "name": "设备等待(自动)", "kind": "unplanned"},
    {"code": "AUTO-CLEAN", "name": "设备清洁(自动)", "kind": "planned"},
    {"code": "AUTO-MAINT", "name": "设备保养(自动)", "kind": "planned"},
]


def ensure_system_downtime_reasons(db) -> None:
    """幂等创建 + 激活系统停机原因（启动时调用）。"""
    from sqlalchemy import select

    for spec in SYSTEM_DOWNTIME_REASONS:
        r = db.execute(
            select(DowntimeReason).where(DowntimeReason.code == spec["code"])
        ).scalar_one_or_none()
        if r is None:
            r = DowntimeReason(
                code=spec["code"], name=spec["name"], kind=spec["kind"],
                is_active=True, is_system=True,
            )
            db.add(r)
        else:
            r.is_active = True
    db.flush()


def register(app: FastAPI) -> None:
    import importlib.util

    if importlib.util.find_spec("lightmes.modules.equipment.router") is None:
        return
    from lightmes.modules.equipment.router import router

    app.include_router(router)
