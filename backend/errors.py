"""Typed application errors and their HTTP translation.

The rule this replaces: the old code wrapped whole handlers in `except
Exception` and returned an empty result, so a broken dependency was
indistinguishable from an empty database. Here, an unexpected exception is
logged with its traceback and returned as a 500 — visible, and traceable by
request id.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.logging_config import get_logger, request_id_var

log = get_logger(__name__)


class AppError(Exception):
    """Base for errors the application raises deliberately."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"
    message: str = "Request could not be completed."

    def __init__(self, message: str | None = None, **context: Any) -> None:
        self.message = message or self.message
        self.context = context
        super().__init__(self.message)


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Not found."


class NotAuthenticated(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"
    message = "Sign in to continue."


class NotAuthorized(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "not_authorized"
    message = "You don't have access to that."


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "That conflicts with something that already exists."


class RateLimited(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "That's a few too many for now. Try again shortly."

    def __init__(self, message: str | None = None, *, retry_after: int | None = None, **context: Any) -> None:
        super().__init__(message, **context)
        self.retry_after = retry_after


class ServiceUnavailable(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A service this needs is unavailable. Try again shortly."


def _payload(code: str, message: str) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    request_id = request_id_var.get()
    if request_id:
        body["error"]["request_id"] = request_id
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", code=exc.code, message=exc.message, **exc.context)
        headers = {}
        # The one piece of machine-readable advice in the error protocol: a
        # client that backs off blindly is indistinguishable from one that
        # hammers, and Retry-After is what tells them apart.
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message),
            headers=headers or None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content=_payload("http_error", detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Project to the three fields a client can act on. Pydantic's raw errors
        # carry a `ctx` holding the original exception object, which is not
        # JSON-serialisable, and an `input` echoing what was submitted — which
        # would put passwords in error responses.
        fields = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                "message": error.get("msg", "Invalid value."),
                "type": error.get("type", "invalid"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_payload("invalid_request", "Some fields need fixing.") | {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path, method=request.method)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("internal_error", "Something went wrong on our end."),
        )
