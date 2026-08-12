import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy.orm import Session

from lightmes.database import SessionLocal
from lightmes.modules.api_v1.models import ApiCallLog

# 写操作记录；失败记录；GET 成功不记录
_WRITTEN_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

# 需要从错误日志中脱敏的模式
_REDACT_PATTERNS = [
    (re.compile(r"lmk_(live|test)_[A-Za-z0-9_-]+"), r"lmk_\1_REDACTED"),
    (re.compile(r"(password|pwd|secret|token)([\"'=:]?\s*)(\S+)", re.IGNORECASE), r"\1\2REDACTED"),
]


def sanitize_error_detail(detail: str | None) -> str | None:
    """Redact sensitive patterns from error detail before logging."""
    if detail is None:
        return None
    redacted = detail
    for pattern, replacement in _REDACT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted[:200]  # Truncate to column size


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 8 字符 trace_id，注入 request.state + X-Trace-Id 响应头。"""

    async def dispatch(self, request: Request, call_next):
        request.state.trace_id = uuid.uuid4().hex[:8]
        response: Response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response


class ApiCallLogMiddleware(BaseHTTPMiddleware):
    """选择性记录 /api/v1/* 调用：写操作 + 错误（>=400）；成功 GET 不记录。"""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)

        should_log = (
            request.method in _WRITTEN_METHODS
            or response.status_code >= 400
        )
        if not should_log:
            return response

        # 提取错误详情（仅 4xx/5xx）
        error_detail = None
        if response.status_code >= 400:
            try:
                # response.body may have been consumed; Starlette allows re-read via .body
                body = response.body
                if body:
                    import json
                    data = json.loads(body)
                    error_detail = str(data.get("detail") or "")
            except Exception:
                error_detail = None

        # 脱敏 + 截断（防止泄露 API key、密码、token 等敏感信息）
        error_detail = sanitize_error_detail(error_detail)

        # 异步写 log（独立 session 避免污染请求 session）
        try:
            trace_id = getattr(request.state, "trace_id", None)
            api_key_id = getattr(request.state, "api_key_id", None)
            user_id = getattr(request.state, "api_key_user_id", None)
            client_ip = request.client.host if request.client else None
            db: Session = SessionLocal()
            try:
                db.add(ApiCallLog(
                    api_key_id=api_key_id, user_id=user_id,
                    method=request.method, path=request.url.path,
                    status_code=response.status_code, duration_ms=duration_ms,
                    trace_id=trace_id, client_ip=client_ip,
                    error_detail=error_detail,
                ))
                db.commit()
            finally:
                db.close()
        except Exception:
            # Logging failures must never break the response
            pass

        return response
