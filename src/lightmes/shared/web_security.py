import secrets
import time
from collections import defaultdict
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from lightmes.config import get_settings


class CsrfMiddleware(BaseHTTPMiddleware):
    """Session-bound CSRF protection for browser HTML forms.

    API and MCP endpoints are intentionally exempt because they use Bearer
    tokens and are not vulnerable to cookie-based cross-site form submission.
    """

    def __init__(self, app, *, exempt_prefixes: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self.exempt_prefixes = exempt_prefixes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if get_settings().environment != "production":
            return await call_next(request)

        if request.url.path.startswith(self.exempt_prefixes):
            return await call_next(request)

        if "session" not in request.scope:
            return await call_next(request)

        if request.method in ("GET", "HEAD", "OPTIONS"):
            token = request.session.get("csrf_token")
            if not token:
                token = secrets.token_urlsafe(32)
                request.session["csrf_token"] = token
            request.state.csrf_token = token
            return await call_next(request)

        expected = request.session.get("csrf_token")
        provided = request.headers.get("x-csrf-token", "")
        if not expected or not secrets.compare_digest(str(expected), provided):
            return JSONResponse({"detail": "CSRF token 校验失败"}, status_code=403)
        request.state.csrf_token = expected
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Small in-memory rate limiter for login and machine-facing endpoints.

    For multi-worker production use a shared Redis-backed limiter; this keeps
    the local/default deployment from being trivially brute-forced.
    """

    def __init__(
        self,
        app,
        *,
        login_limit: int,
        api_limit: int,
        window_seconds: int,
    ) -> None:
        super().__init__(app)
        self.login_limit = login_limit
        self.api_limit = api_limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if get_settings().environment != "production":
            return await call_next(request)

        path = request.url.path
        limit = None
        if path in ("/login", "/api/auth/login"):
            limit = self.login_limit
        elif path.startswith("/api/v1/") or path.startswith("/mcp"):
            limit = self.api_limit

        if limit is None:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path}"
        now = time.monotonic()
        hits = [t for t in self._hits[key] if now - t < self.window_seconds]
        if len(hits) >= limit:
            self._hits[key] = hits
            return JSONResponse(
                {"detail": "请求过于频繁，请稍后重试"},
                status_code=429,
                headers={"Retry-After": str(self.window_seconds)},
            )
        hits.append(now)
        self._hits[key] = hits
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, enable_hsts: bool) -> None:
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.enable_hsts:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response
