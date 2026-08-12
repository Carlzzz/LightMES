from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lightmes.shared.errors import (
    BusinessRuleError, ConflictError, DomainError, NotFoundError, ValidationError,
)

_TITLE_MAP = {
    ValidationError: "Bad Request",
    NotFoundError: "Not Found",
    ConflictError: "Conflict",
    BusinessRuleError: "Unprocessable Entity",
}


def _title_for(exc: DomainError) -> str:
    return _TITLE_MAP.get(type(exc), "Error")


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def register_problem_details_handler(app: FastAPI) -> None:
    """Register RFC 7807 Problem Details JSON handler for DomainError.

    Returns JSON with Content-Type 'application/problem+json' containing:
        type, title, status, detail, instance, trace_id
    """
    @app.exception_handler(DomainError)
    async def _handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": f"https://lightmes/errors/{type(exc).__name__}",
                "title": _title_for(exc),
                "status": exc.status_code,
                "detail": exc.detail,
                "instance": str(request.url.path),
                "trace_id": _trace_id(request),
            },
            media_type="application/problem+json",
        )
