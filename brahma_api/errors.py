"""The OpenAI error envelope, and the handlers that make FastAPI speak it.

A client library decides whether to retry from the status code, and decides what
to show a person from ``error.code``. Getting either wrong turns a transient
provider hiccup into a hard failure, so the mapping lives in one place
(``docs/OPENAI-COMPATIBLE-API-DESIGN.md`` §5.13) rather than at each route.

FastAPI is imported inside :func:`install_handlers` only. ``models.py`` and
``translate.py`` raise :class:`ApiError` without pulling the web framework into
unit tests that never start a server.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ApiError(Exception):
    """One wire-visible failure: status, code, and the parameter to blame."""

    status: int
    message: str
    type: str = "invalid_request_error"
    param: Optional[str] = None
    code: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Exception.__init__ is not called by @dataclass; keep repr/str useful
        # when this escapes a handler (logs, tests, a raised traceback).
        Exception.__init__(self, self.message)

    def body(self) -> Dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "param": self.param,
                "code": self.code,
            }
        }


def invalid(
    message: str,
    *,
    param: Optional[str] = None,
    code: str = "invalid_value",
) -> ApiError:
    return ApiError(400, message, "invalid_request_error", param, code)


def unauthorized(
    message: str = "Incorrect API key provided.",
    *,
    code: str = "invalid_api_key",
) -> ApiError:
    return ApiError(401, message, "invalid_request_error", None, code)


def forbidden(message: str, *, code: str = "insufficient_scope") -> ApiError:
    return ApiError(403, message, "permission_denied", None, code)


def not_found(message: str, *, code: str = "model_not_found") -> ApiError:
    return ApiError(404, message, "invalid_request_error", None, code)


def too_large(message: str) -> ApiError:
    return ApiError(413, message, "invalid_request_error", None, "payload_too_large")


def conflict(message: str, *, code: str = "session_busy") -> ApiError:
    return ApiError(409, message, "invalid_request_error", None, code)


def rate_limited(message: str = "Rate limit exceeded.", *, retry_after: int = 5) -> ApiError:
    return ApiError(
        429,
        message,
        "rate_limit_error",
        None,
        "rate_limit_exceeded",
        headers={"Retry-After": str(retry_after)},
    )


def server_error(message: str = "Internal server error.", *, code: str = "internal_error") -> ApiError:
    return ApiError(500, message, "server_error", None, code)


def unavailable(message: str) -> ApiError:
    return ApiError(503, message, "server_error", None, "provider_unavailable")


def install_handlers(app: Any) -> None:
    """Register the envelope handlers on a FastAPI app."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(ApiError)
    async def _api_error(_request: Any, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Any, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's 422 body is not an OpenAI error envelope. Collapse it to
        # 400 and name the first offending field: that is what a client SDK
        # expects from `param`.
        errors = exc.errors() or []
        first = errors[0] if errors else {}
        loc = [str(part) for part in (first.get("loc") or []) if part != "body"]
        param = ".".join(loc) or None
        detail = str(first.get("msg") or "invalid request body")
        return JSONResponse(
            ApiError(400, detail, "invalid_request_error", param, "invalid_value").body(),
            status_code=400,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Any, exc: StarletteHTTPException) -> JSONResponse:
        # 404 for an unknown route, 405 for a wrong method: keep the shape so a
        # client's error parser never sees a bare Starlette body.
        if exc.status_code == 404:
            err = not_found("Unknown route.", code="unknown_route")
        else:
            err = ApiError(
                exc.status_code,
                str(exc.detail),
                "invalid_request_error",
                None,
                "http_error",
            )
        return JSONResponse(err.body(), status_code=err.status)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Any, exc: Exception) -> JSONResponse:
        # The message is deliberately generic: a traceback can contain a token,
        # a path, or a provider key. The audit log and the console have it.
        logger.exception("brahma_api unhandled error: %s", exc)
        err = ApiError(
            500,
            "The server hit an internal error. See the Brahma audit log.",
            "server_error",
            None,
            "internal_error",
        )
        return JSONResponse(err.body(), status_code=500)
