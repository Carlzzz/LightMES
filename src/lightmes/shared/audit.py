from __future__ import annotations

import contextvars
from dataclasses import dataclass

from sqlalchemy import JSON, String
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped, mapped_column
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from lightmes.shared.base import Base, TimestampMixin


@dataclass(frozen=True)
class AuditContext:
    user_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None


_audit_context: contextvars.ContextVar[AuditContext] = contextvars.ContextVar(
    "lightmes_audit_context", default=AuditContext()
)


def set_audit_user(user_id: int | None) -> None:
    current = _audit_context.get()
    _audit_context.set(
        AuditContext(
            user_id=user_id,
            ip_address=current.ip_address,
            user_agent=current.user_agent,
        )
    )


def audit_user_from_request(request: Request) -> int | None:
    if "session" in request.scope:
        user_id = request.session.get("user_id")
        if user_id is not None:
            return user_id

    user_id = getattr(request.state, "api_key_user_id", None)
    if user_id is not None:
        return user_id

    user = getattr(request.state, "user", None)
    if user is not None:
        return getattr(user, "id", None)

    return None


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(default=None, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[int | None] = mapped_column(default=None, index=True)
    action: Mapped[str] = mapped_column(String(50))
    before_state: Mapped[dict | None] = mapped_column(JSON, default=None)
    after_state: Mapped[dict | None] = mapped_column(JSON, default=None)
    ip_address: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)


_SENSITIVE_FIELDS = {
    "password",
    "password_hash",
    "remember_token",
    "key_hash",
    "password_encrypted",
    "api_config",
}


def _serialize(value):
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items() if str(k) not in _SENSITIVE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _attributes(obj) -> dict:
    return {
        k: _serialize(v)
        for k, v in obj.__dict__.items()
        if not k.startswith("_") and k not in _SENSITIVE_FIELDS
    }


def _changed_fields(obj) -> tuple[dict, dict]:
    state = sa_inspect(obj)
    before = {}
    after = {}
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in _SENSITIVE_FIELDS:
            continue
        hist = state.get_history(key, passive=True)
        if hist.has_changes():
            before[key] = _serialize(hist.deleted[0] if hist.deleted else None)
            after[key] = _serialize(hist.added[0] if hist.added else None)
    return before, after


def _write_audit(connection, target, action: str, before: dict | None, after: dict | None):
    ctx = _audit_context.get()
    connection.execute(
        AuditLog.__table__.insert().values(
            user_id=ctx.user_id,
            entity_type=target.__class__.__name__,
            entity_id=target.id,
            action=action,
            before_state=before,
            after_state=after,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
    )


def _on_insert(mapper, connection, target) -> None:
    _write_audit(connection, target, "created", None, _attributes(target))


def _on_update(mapper, connection, target) -> None:
    before, after = _changed_fields(target)
    if before or after:
        _write_audit(connection, target, "updated", before, after)


def _on_delete(mapper, connection, target) -> None:
    _write_audit(connection, target, "deleted", _attributes(target), None)


def register_audit_listeners(models: tuple[type, ...]) -> None:
    for model in models:
        event.listen(model, "after_insert", _on_insert)
        event.listen(model, "after_update", _on_update)
        event.listen(model, "after_delete", _on_delete)


class AuditContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        user_id = audit_user_from_request(request)
        ctx = AuditContext(
            user_id=user_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        token = _audit_context.set(ctx)
        try:
            response = await call_next(request)
            return response
        finally:
            _audit_context.reset(token)
